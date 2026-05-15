from fastapi import FastAPI, HTTPException, Body, WebSocket, WebSocketDisconnect, Request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from gridding import grid_argo_data
import httpx
import pandas as pd
import numpy as np
import io
import os
import bisect
import json
import re
import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from typing import List, Optional
from pydantic import BaseModel
import xarray as xr
import diskcache
import aiofiles
import aiosqlite
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from filelock import FileLock
import hashlib
import shutil

# ── Global Caches ─────────────────────────────────────────────────────────────
ACTIVE_FLOATS_CACHE = {}  # key: "start_end", value: {'response': dict, 'timestamp': float, 'ttl': 1800}
BGC_PARAM_COUNTS = {'NO3': 0, 'DOXY': 0, 'CHLA': 0, 'BBP700': 0, 'PH': 0}

# ── Async Export Job System ───────────────────────────────────────────────────
import uuid
EXPORT_JOBS = {}  # job_id -> {'status': str, 'progress': int, 'total': int, 'result_path': str, 'error': str, 'format': str, 'filename': str}

async def cleanup_task():
    """Background task to enforce 20GB size limit on downloads directory (LRU)."""
    MAX_SIZE = 20 * 1024 * 1024 * 1024 # 20 GB
    while True:
        try:
            total_size = 0
            files = []
            for f in os.listdir(DOWNLOADS_DIR):
                path = os.path.join(DOWNLOADS_DIR, f)
                if os.path.isfile(path) and not path.endswith('.tmp') and not path.endswith('.lock'):
                    stat = os.stat(path)
                    total_size += stat.st_size
                    files.append((path, stat.st_atime))
            
            if total_size > MAX_SIZE:
                # Sort by access time (oldest first)
                files.sort(key=lambda x: x[1])
                bytes_to_free = total_size - MAX_SIZE + (1 * 1024 * 1024 * 1024) # Free down to 19GB
                freed = 0
                for path, _ in files:
                    if freed >= bytes_to_free:
                        break
                    try:
                        size = os.path.getsize(path)
                        os.remove(path)
                        freed += size
                    except Exception:
                        pass
        except Exception as e:
            print(f"Cleanup task error: {e}")
        
        await asyncio.sleep(3600) # Run every hour

@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
        timeout=httpx.Timeout(120.0, connect=30.0),
    )
    await init_db()
    # Precompute active floats in background — don't block server startup
    asyncio.create_task(precompute_active_floats())
    task = asyncio.create_task(cleanup_task())
    yield
    task.cancel()
    if HTTP_CLIENT:
        await HTTP_CLIENT.aclose()
    PROCESS_POOL.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)

# Rate Limiting setup
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Restriction (use env var or default to localhost)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
LOCAL_INDEX_PATH = 'ar_index_global_prof.txt'
REMOTE_INDEX_URL = 'https://data-argo.ifremer.fr/ar_index_global_prof.txt'
DOWNLOADS_DIR = 'downloads'
BIO_INDEX_PATH = 'argo_bio-profile_index.txt'
META_INDEX_PATH = 'ar_index_global_meta.txt'
REMOTE_META_URL = 'https://data-argo.ifremer.fr/ar_index_global_meta.txt'
# Ensure downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Global BGC Platforms set (still useful for quick lookup, loaded from DB)
BGC_PLATFORMS = set()

async def init_db():
    """Initializes SQLite database and syncs from .txt files if needed."""
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL mode for concurrent read performance
        await db.execute('PRAGMA journal_mode=WAL')
        await db.execute('PRAGMA cache_size=-64000')  # 64MB cache
        await db.execute('PRAGMA synchronous=NORMAL')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                file TEXT PRIMARY KEY,
                platform TEXT,
                date TEXT,
                lat REAL,
                lon REAL,
                ocean TEXT,
                profiler_type TEXT,
                institution TEXT,
                type TEXT
            )
        ''')
        # Metadata table — source of truth for float-to-institution mapping
        await db.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                platform TEXT PRIMARY KEY,
                profiler_type TEXT,
                institution TEXT,
                date_update TEXT
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_date ON profiles(date)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_type ON profiles(type)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_geo ON profiles(lat, lon)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_platform_date ON profiles(platform, date)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_meta_inst ON metadata(institution)')
        await db.commit()

        # Only sync if DB is empty — never resync automatically on mtime
        cursor = await db.execute('SELECT COUNT(*) FROM profiles')
        count = (await cursor.fetchone())[0]
        if count == 0:
            print("DB empty — syncing from index files...")
            await sync_index_to_db(db)

        cursor = await db.execute('SELECT COUNT(*) FROM metadata')
        meta_count = (await cursor.fetchone())[0]
        if meta_count == 0:
            print("Metadata empty — syncing from ar_index_global_meta.txt...")
            await sync_metadata_to_db(db)
            
        # Load BGC platforms into memory for quick lookup
        cursor = await db.execute("SELECT DISTINCT platform FROM profiles WHERE type='bio'")
        async for row in cursor:
            BGC_PLATFORMS.add(row[0])
        print(f"Loaded {len(BGC_PLATFORMS)} BGC platforms")
        
        # Log metadata counts for verification
        cursor = await db.execute("SELECT COUNT(*) FROM metadata")
        total_meta = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM metadata WHERE institution = 'IN'")
        incois_meta = (await cursor.fetchone())[0]
        print(f"Metadata: {total_meta} total floats, {incois_meta} INCOIS floats")

    # Load BGC parameter coverage counts from bio index file
    await load_bgc_parameter_counts()


async def load_bgc_parameter_counts():
    """Scans argo_bio-profile_index.txt to count platforms per BGC parameter."""
    global BGC_PARAM_COUNTS
    if not os.path.exists(BIO_INDEX_PATH):
        return
    platform_params = {}  # platform -> set of param names
    with open(BIO_INDEX_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or line.startswith('file,') or not line.strip():
                continue
            parts = line.split(',')
            if len(parts) >= 8:
                try:
                    platform = parts[0].split('/')[1]
                    params_str = parts[7].strip() if len(parts) > 7 else ''
                    if platform not in platform_params:
                        platform_params[platform] = set()
                    for p in params_str.split():
                        platform_params[platform].add(p.upper())
                except (IndexError, ValueError):
                    continue
    counts = {'NO3': 0, 'DOXY': 0, 'CHLA': 0, 'BBP700': 0, 'PH': 0}
    for platform, pset in platform_params.items():
        if 'NITRATE' in pset or 'NO3' in pset:
            counts['NO3'] += 1
        if 'DOXY' in pset:
            counts['DOXY'] += 1
        if 'CHLA' in pset:
            counts['CHLA'] += 1
        if 'BBP700' in pset:
            counts['BBP700'] += 1
        if 'PH_IN_SITU_TOTAL' in pset or 'PH' in pset:
            counts['PH'] += 1
    BGC_PARAM_COUNTS = counts
    print(f"BGC parameter coverage: {counts}")


async def precompute_active_floats():
    """Precomputes the global active floats cache at startup."""
    global ACTIVE_FLOATS_CACHE
    print("Precomputing global active floats cache...")
    t0 = time.time()
    response = await _build_active_floats_response(None, None)
    ACTIVE_FLOATS_CACHE['global'] = {
        'response': response,
        'timestamp': time.time(),
        'ttl': 1800
    }
    elapsed = time.time() - t0
    print(f"Active floats cache ready: {response['count']} platforms in {elapsed:.1f}s")

async def sync_index_to_db(db):
    """Parses .txt files and inserts into SQLite."""
    await ensure_index_files()
    
    for ptype, path in [('core', LOCAL_INDEX_PATH), ('bio', BIO_INDEX_PATH)]:
        print(f"Parsing {ptype} index...")
        # Use synchronous file reading for massive speedup (avoids millions of async thread switches)
        with open(path, mode='r', encoding='utf-8') as f:
            batch = []
            for line in f:
                if line.startswith('#') or 'file,' in line:
                    continue
                parts = line.split(',')
                if ptype == 'core' and len(parts) >= 8:
                    try:
                        platform = parts[0].split('/')[1]
                        batch.append((parts[0], platform, parts[1], float(parts[2]), float(parts[3]), parts[4], parts[5], parts[6], 'core'))
                    except (ValueError, IndexError): continue
                elif ptype == 'bio' and len(parts) >= 7:
                    try:
                        platform = parts[0].split('/')[1]
                        batch.append((parts[0], platform, parts[1], float(parts[2]), float(parts[3]), parts[4], parts[5], parts[6], 'bio'))
                    except (ValueError, IndexError): continue
                
                if len(batch) >= 5000:
                    await db.executemany('INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?,?,?,?,?)', batch)
                    batch = []
            if batch:
                await db.executemany('INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?,?,?,?,?)', batch)
    await db.commit()

async def sync_metadata_to_db(db):
    """Parses ar_index_global_meta.txt and inserts into the metadata table.
    
    Format: file,profiler_type,institution,date_update
    Example: aoml/13857/13857_meta.nc,845,AO,20181011200014
    
    The platform ID is extracted from the file path (e.g., '13857' from 'aoml/13857/13857_meta.nc').
    This is the authoritative source for which institution deployed each float.
    """
    if not os.path.exists(META_INDEX_PATH):
        print(f"WARNING: {META_INDEX_PATH} not found, skipping metadata sync")
        return
    
    await db.execute('DELETE FROM metadata')  # Full refresh
    
    batch = []
    count = 0
    # Use synchronous file reading for speed
    with open(META_INDEX_PATH, mode='r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or line.startswith('file,') or not line:
                continue
            parts = line.split(',')
            if len(parts) >= 4:
                try:
                    # Extract platform ID from path like 'aoml/13857/13857_meta.nc'
                    file_path = parts[0]
                    path_parts = file_path.split('/')
                    if len(path_parts) >= 2:
                        platform = path_parts[1]  # e.g., '13857'
                    else:
                        continue
                    profiler_type = parts[1].strip()
                    institution = parts[2].strip()
                    date_update = parts[3].strip()
                    batch.append((platform, profiler_type, institution, date_update))
                    count += 1
                except (ValueError, IndexError):
                    continue
            
            if len(batch) >= 5000:
                await db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?,?,?)', batch)
                batch = []
    
    if batch:
        await db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?,?,?)', batch)
    await db.commit()
    print(f"Synced {count} metadata entries")

async def ensure_index_files():
    """Downloads index files if missing or old."""
    for url, path in [
        (REMOTE_INDEX_URL, LOCAL_INDEX_PATH), 
        ('https://data-argo.ifremer.fr/argo_bio-profile_index.txt', BIO_INDEX_PATH),
        (REMOTE_META_URL, META_INDEX_PATH)
    ]:
        if not os.path.exists(path) or (time.time() - os.path.getmtime(path)) > 86400:
            print(f"Downloading {path}...")
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=600.0)
                async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
                    await f.write(resp.text)

async def db_query_profiles(start_date, end_date, ptype, bounds=None):
    """Queries the SQLite database for profiles."""
    start_str = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d") + "000000"
    end_str = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d") + "235959"
    
    query = "SELECT * FROM profiles WHERE date BETWEEN ? AND ? AND type = ? AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180"
    params = [start_str, end_str, ptype]
    
    if bounds:
        query += " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        params.extend([bounds['south'], bounds['north'], bounds['west'], bounds['east']])
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

# ── Shared HTTP client & Process Pool ─────────────────────────────────────────
HTTP_CLIENT: httpx.AsyncClient = None
PROCESS_POOL = ProcessPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) + 4))
EXTRACTION_SEMAPHORE = asyncio.Semaphore(min(16, (os.cpu_count() or 4) * 2))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(100) # Global download limit
MAX_PROFILES_PER_REQUEST = 50000  # Support large 20-year queries
BATCH_CHUNK_SIZE = 200  # Larger chunks for throughput
MAX_DOWNLOAD_RETRIES = 3

# Cache and DB paths
CACHE_DIR = 'cache'
DB_PATH = 'argo_index.db'
os.makedirs(CACHE_DIR, exist_ok=True)
cache = diskcache.Cache(CACHE_DIR, size_limit=20 * 1024 * 1024 * 1024) # 20GB limit


class SearchParams(BaseModel):
    startDate: str
    endDate: str
    minDepth: float
    maxDepth: float
    type: str # 'core' or 'bio'

class Bounds(BaseModel):
    north: float
    south: float
    east: float
    west: float

class ProcessRequest(BaseModel):
    bounds: Bounds
    params: SearchParams

class GridRequest(BaseModel):
    bounds: Bounds
    params: SearchParams
    variable: str = 'TEMP'
    depth_level: float = 10.0
    depth_tolerance: float = 50.0
    resolution: float = 0.5
    method: str = 'oi'  # 'oi', 'linear', 'nearest'
    corr_length: float = 2.0
    snr: float = 1.0

class ExportRequest(BaseModel):
    bounds: Bounds
    params: SearchParams
    selectedVars: Optional[List[str]] = None

class DivaExportRequest(BaseModel):
    bounds: Bounds
    params: SearchParams
    variable: str = 'TEMP'
    depth_level: float = 10.0
    depth_tolerance: float = 50.0
    resolution: float = 0.5
    method: str = 'oi'
    corr_length: float = 2.0
    snr: float = 1.0




async def download_with_retry(file_path):
    """Download a NetCDF file with retry, exponential backoff, and file locking."""
    url = f"https://data-argo.ifremer.fr/dac/{file_path}"
    filename = os.path.basename(file_path)
    local_path = os.path.join(DOWNLOADS_DIR, filename)
    lock_path = local_path + ".lock"
    tmp_path = local_path + ".tmp"

    if os.path.exists(local_path):
        return local_path

    # Use a file-based lock to prevent multiple processes from downloading the same file
    with FileLock(lock_path):
        if os.path.exists(local_path):
            return local_path

        for attempt in range(MAX_DOWNLOAD_RETRIES):
            try:
                async with DOWNLOAD_SEMAPHORE:
                    resp = await HTTP_CLIENT.get(url)
                    resp.raise_for_status()
                    # Atomic write using a temporary file
                    async with aiofiles.open(tmp_path, mode='wb') as f:
                        await f.write(resp.content)
                    
                    # Move temp file to final location
                    os.replace(tmp_path, local_path)
                    return local_path
            except Exception as e:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if attempt < MAX_DOWNLOAD_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise e
    return local_path

class ParamsObj:
    def __init__(self, d):
        self.__dict__ = d

def process_netcdf(file_path, params):
    """Extracts ALL data from NetCDF file using xarray with High Accuracy."""
    try:
        ds = xr.open_dataset(file_path)
        data = []
        if 'PRES' not in ds:
            ds.close()
            return []
        pres = ds['PRES'].values
        
        # Extract ALL variables from NetCDF file
        vars_to_extract = {}
        flags_to_extract = {}
        
        # Get measurement variables that share PRES dimensions (N_PROF, N_LEVELS)
        # This excludes per-profile metadata (CYCLE_NUMBER, LATITUDE, etc.) and
        # history variables that have different dimensions
        pres_dims = ds['PRES'].dims  # e.g. ('N_PROF', 'N_LEVELS')
        for var_name in ds.data_vars:
            if var_name not in ['PRES'] and not var_name.endswith('_QC'):
                try:
                    var = ds[var_name]
                    var_data = var.values
                    # Only include numeric variables with same dims as PRES
                    if (var_data.size > 0 and 
                        var_data.dtype.kind in ('f', 'i', 'u') and
                        var.dims == pres_dims):
                        vars_to_extract[var_name] = var_data
                        qc_key = f"{var_name}_QC"
                        if qc_key in ds:
                            qc_data = ds[qc_key].values
                            if qc_data.size > 0:
                                flags_to_extract[qc_key] = qc_data
                except Exception:
                    continue  # Skip problematic variables
        
        # Always include PRES
        vars_to_extract['PRES'] = pres
        if 'PRES_QC' in ds:
            try:
                pres_qc = ds['PRES_QC'].values
                if pres_qc.size > 0:
                    flags_to_extract['PRES_QC'] = pres_qc
            except Exception:
                pass

        # Handle different array dimensions safely
        if pres.ndim == 1:
            # 1D case - single profile
            n_levels = pres.shape[0]
            for l in range(n_levels):
                try:
                    p_val = pres[l]
                    if np.isnan(p_val): continue
                    
                    depth = float(p_val)
                    if params.minDepth <= depth <= params.maxDepth:
                        row = {'depth': depth}
                        for vname, vdata in vars_to_extract.items():
                            try:
                                if vdata.ndim == 1 and l < len(vdata):
                                    val = vdata[l]
                                elif vdata.ndim == 2 and vdata.shape[1] > l:
                                    val = vdata[0, l]
                                else:
                                    val = np.nan
                                row[vname] = float(val) if not np.isnan(val) else ''
                            except (IndexError, ValueError, TypeError):
                                row[vname] = ''
                        
                        for qname, qdata in flags_to_extract.items():
                            try:
                                if qdata.ndim == 1 and l < len(qdata):
                                    val = qdata[l]
                                elif qdata.ndim == 2 and qdata.shape[1] > l:
                                    val = qdata[0, l]
                                else:
                                    val = ''
                                if isinstance(val, (bytes, np.bytes_)):
                                    row[qname] = val.decode('utf-8')
                                else:
                                    row[qname] = str(val) if val != '' else ''
                            except (IndexError, ValueError, UnicodeDecodeError):
                                row[qname] = ''
                        data.append(row)
                except Exception:
                    continue
                    
        elif pres.ndim == 2:
            # 2D case - multiple profiles
            n_prof, n_levels = pres.shape
            for p in range(n_prof):
                for l in range(n_levels):
                    try:
                        p_val = pres[p, l]
                        if np.isnan(p_val): continue
                        
                        depth = float(p_val)
                        if params.minDepth <= depth <= params.maxDepth:
                            row = {'depth': depth}
                            for vname, vdata in vars_to_extract.items():
                                try:
                                    if vdata.ndim >= 2 and p < vdata.shape[0] and l < vdata.shape[1]:
                                        val = vdata[p, l]
                                    elif vdata.ndim == 1 and p < len(vdata):
                                        val = vdata[p]
                                    else:
                                        val = np.nan
                                    row[vname] = float(val) if not np.isnan(val) else ''
                                except (IndexError, ValueError, TypeError):
                                    row[vname] = ''
                            
                            for qname, qdata in flags_to_extract.items():
                                try:
                                    if qdata.ndim >= 2 and p < qdata.shape[0] and l < qdata.shape[1]:
                                        val = qdata[p, l]
                                    elif qdata.ndim == 1 and p < len(qdata):
                                        val = qdata[p]
                                    else:
                                        val = ''
                                    if isinstance(val, (bytes, np.bytes_)):
                                        row[qname] = val.decode('utf-8')
                                    else:
                                        row[qname] = str(val) if val != '' else ''
                                except (IndexError, ValueError, UnicodeDecodeError):
                                    row[qname] = ''
                            data.append(row)
                    except Exception:
                        continue
        
        ds.close()
        return data
    except Exception as e:
        print(f"Error processing NetCDF: {e}")
        return []

def extract_metadata(filename):
    """Extracts Platform and Cycle from filename e.g. R1901839_334.nc"""
    match = re.search(r'([A-Z]*)([0-9]+)_([0-9]+D?)', filename)
    if match:
        return match.group(2), match.group(3)
    return "", ""

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        req_dict = json.loads(data)
        
        bounds = req_dict['bounds']
        params = req_dict['params']
        
        # Ensure numeric types are properly converted (they arrive as strings from JSON)
        params['minDepth'] = float(params.get('minDepth', 0))
        params['maxDepth'] = float(params.get('maxDepth', 2000))

        # Safely coerce bounds to floats — sidebar inputs may send empty strings ''
        def safe_float(val, default):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        raw_north = safe_float(bounds.get('north'), 90.0)
        raw_south = safe_float(bounds.get('south'), -90.0)
        raw_east  = safe_float(bounds.get('east'),  180.0)
        raw_west  = safe_float(bounds.get('west'),  -180.0)

        # Auto-fix inverted bounds (swap if south > north or west > east)
        bounds = {
            'north': max(raw_north, raw_south),
            'south': min(raw_north, raw_south),
            'east':  max(raw_east, raw_west),
            'west':  min(raw_east, raw_west),
        }
        print(f"[DEBUG] Bounds (normalized): N={bounds['north']:.4f} S={bounds['south']:.4f} E={bounds['east']:.4f} W={bounds['west']:.4f}")
        
        # Params object wrapper for helper functions
        params_obj = ParamsObj(params)

        await websocket.send_json({"type": "log", "message": "Initializing Search..."})
        #await load_index()
        
        # 1. DB Search (Combined Date & Geo)
        filtered = await db_query_profiles(
            params['startDate'], 
            params['endDate'],
            params['type'],
            bounds)

        if len(filtered) > MAX_PROFILES_PER_REQUEST:
            await websocket.send_json({"type": "error", "message": f"Request too large ({len(filtered)} profiles). Please narrow your date or area. Max is {MAX_PROFILES_PER_REQUEST}."})
            return

        await websocket.send_json({"type": "log", "message": f"Found {len(filtered)} profiles in DB."})
        
        if not filtered:
            await websocket.send_json({"type": "error", "message": "No profiles found in selected area/date range."})
            return

        selection = filtered
        all_results = []
        
        TOTAL = len(selection)
        unique_floats = len(set(
            extract_metadata(os.path.basename(p['file']))[0] for p in selection
        ))
        
        await websocket.send_json({"type": "log", "message": f"Processing {TOTAL} profiles from {unique_floats} unique floats..."})

        # ── Chunked Batch Processing ──────────────────────────────────────
        loop = asyncio.get_event_loop()
        downloaded_count = 0
        extracted_count = 0
        error_count = 0
        
        for chunk_start in range(0, TOTAL, BATCH_CHUNK_SIZE):
            chunk_end = min(chunk_start + BATCH_CHUNK_SIZE, TOTAL)
            chunk = selection[chunk_start:chunk_end]
            chunk_num = (chunk_start // BATCH_CHUNK_SIZE) + 1
            total_chunks = (TOTAL + BATCH_CHUNK_SIZE - 1) // BATCH_CHUNK_SIZE
            
            await websocket.send_json({"type": "log", "message": f"⬇ Batch {chunk_num}/{total_chunks}: Downloading {len(chunk)} files..."})

            # Step A: Download chunk concurrently with retry
            async def download_one(profile):
                try:
                    path = await download_with_retry(profile['file'])
                    return profile, path
                except Exception as e:
                    return profile, e

            download_results = await asyncio.gather(*[download_one(p) for p in chunk])
            
            success_paths = []
            for profile, result in download_results:
                if isinstance(result, Exception):
                    error_count += 1
                    continue
                success_paths.append((profile, result))
            
            downloaded_count += len(success_paths)
            
            await websocket.send_json({"type": "log", "message": f"📊 Batch {chunk_num}/{total_chunks}: Extracting from {len(success_paths)} files... ({downloaded_count}/{TOTAL} total)"})

            # Step B: Extract NetCDF data in parallel using process pool
            for profile, local_path in success_paths:
                try:
                    extracted = await loop.run_in_executor(
                        PROCESS_POOL, process_netcdf, local_path, params_obj
                    )
                    
                    filename = os.path.basename(profile['file'])
                    platform, cycle = extract_metadata(filename)
                    
                    for row in extracted:
                        row.update({
                            'Date': profile['date'],
                            'Latitude': profile['lat'],
                            'Longitude': profile['lon'],
                            'Platform': platform,
                            'Cycle': cycle,
                            'Institution': profile.get('institution', ''),
                            'Ocean': profile.get('ocean', ''),
                            'File': filename
                        })
                        all_results.append(row)
                    extracted_count += 1
                except Exception as e:
                    error_count += 1
            
            await websocket.send_json({"type": "log", "message": f"✅ Batch {chunk_num}/{total_chunks} complete. {len(all_results)} data rows so far."})

        # ── Final Stats ───────────────────────────────────────────────────
        if error_count > 0:
            await websocket.send_json({"type": "log", "message": f"⚠ {error_count} files had errors and were skipped."})

        if not all_results:
            await websocket.send_json({"type": "error", "message": "No data extracted from profiles."})
            return
            
        await websocket.send_json({"type": "log", "message": f"Generating CSV from {len(all_results)} rows across {extracted_count} profiles..."})
        
        df = pd.DataFrame(all_results)
        
        selected_vars = params.get('selectedVars', [])
        filter_vars = len(selected_vars) > 0 and 'All Available Parameters' not in selected_vars
        include_qc = 'All QC Flags' in selected_vars or not filter_vars
        
        first_cols = ['Platform', 'Cycle', 'Date', 'Latitude', 'Longitude', 'depth']
        meta_cols = ['Institution', 'Ocean', 'File']

        def keep_col(c):
            if not filter_vars:
                return True
            base_c = c.replace('_ADJUSTED', '')
            return base_c in selected_vars

        # Proper Column Ordering with enhanced metadata
        if params.get('type') == 'bio':
            bgc_priority = [
                'CHLA', 'CHLA_ADJUSTED', 
                'DOXY', 'DOXY_ADJUSTED', 
                'NITRATE', 'NITRATE_ADJUSTED', 
                'PH', 'PH_ADJUSTED', 
                'BBP700', 'BBP700_ADJUSTED', 
                'IRRADIANCE', 'IRRADIANCE_ADJUSTED',
                'TEMP', 'TEMP_ADJUSTED',
                'PSAL', 'PSAL_ADJUSTED',
                'PRES', 'PRES_ADJUSTED'
            ]
            available_priority = [c for c in bgc_priority if c in df.columns and keep_col(c)]
            other_param_cols = [c for c in df.columns if c not in first_cols and c not in available_priority and 'QC' not in c and c not in meta_cols and keep_col(c)]
            qc_cols = [c for c in df.columns if 'QC' in c] if include_qc else []
            final_cols = first_cols + available_priority + other_param_cols + qc_cols + meta_cols
        else:
            param_cols = [c for c in df.columns if c not in first_cols and 'QC' not in c and c not in meta_cols and keep_col(c)]
            qc_cols = [c for c in df.columns if 'QC' in c] if include_qc else []
            final_cols = first_cols + param_cols + qc_cols + meta_cols
        
        existing_cols = [c for c in final_cols if c in df.columns]
        df = df[existing_cols]
        
        df.replace('', np.nan, inplace=True)
        df.dropna(axis=1, how='all', inplace=True)
        df.rename(columns={'depth': 'Depth (dbar)'}, inplace=True)
            
        # ── Streaming CSV via chunked WebSocket messages ──────────────────
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        csv_content = csv_buffer.getvalue()
        
        # For very large CSVs, send in 1MB chunks
        CSV_CHUNK_SIZE = 1_000_000  # 1MB per message
        if len(csv_content) > CSV_CHUNK_SIZE:
            total_csv_chunks = (len(csv_content) + CSV_CHUNK_SIZE - 1) // CSV_CHUNK_SIZE
            await websocket.send_json({"type": "log", "message": f"Sending {len(csv_content)//1024}KB CSV in {total_csv_chunks} chunks..."})
            
            for i in range(0, len(csv_content), CSV_CHUNK_SIZE):
                chunk_data = csv_content[i:i+CSV_CHUNK_SIZE]
                is_last = (i + CSV_CHUNK_SIZE) >= len(csv_content)
                await websocket.send_json({
                    "type": "csv_chunk",
                    "data": chunk_data,
                    "chunk_index": i // CSV_CHUNK_SIZE,
                    "total_chunks": total_csv_chunks,
                    "is_last": is_last,
                    "filename": f"argo_complete_dataset_{params['type']}_{TOTAL}_profiles.csv"
                })
        else:
            await websocket.send_json({
                "type": "complete", 
                "csv": csv_content,
                "filename": f"argo_complete_dataset_{params['type']}_{TOTAL}_profiles.csv"
            })
        
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WS Error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass

async def _build_active_floats_response(startDate: Optional[str], endDate: Optional[str]):
    """Builds the active floats response object. Used by cache and endpoint.
    
    Logic: Always shows floats active within 90 days of endDate.
    The startDate parameter is IGNORED for active float display — the map
    always shows a 90-day lookback window from endDate (or today).
    This ensures that setting date to "today" still shows all active floats.
    """
    
    if not endDate:
        end_dt = datetime.utcnow()
    else:
        end_dt = datetime.strptime(endDate, "%Y-%m-%d")
        
    end_str = end_dt.strftime("%Y%m%d") + "235959"
    
    # ALWAYS look back 90 days from endDate for active float display
    # This is the key fix: startDate does NOT restrict the map view
    ninety_days_ago_dt = end_dt - timedelta(days=90)
    start_str = ninety_days_ago_dt.strftime("%Y%m%d") + "000000"

    # Single DB connection for entire computation
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('PRAGMA journal_mode=WAL')
        db.row_factory = aiosqlite.Row

        # Fast single-pass query: GROUP BY platform with MAX(date) to get latest position.
        # SQLite bare-column guarantee: non-aggregated columns come from the MAX(date) row.
        # No geo filter in SQL (only 0.01% invalid rows) — filter in Python for 13x speedup.
        query = '''
            SELECT file, platform, MAX(date) as date, lat, lon, ocean, 
                   profiler_type, institution, type
            FROM profiles
            WHERE date BETWEEN ? AND ?
            GROUP BY platform
        '''
        async with db.execute(query, [start_str, end_str]) as cursor:
            rows = await cursor.fetchall()

        # Fetch all metadata for institution mapping
        meta_dict = {}
        cursor = await db.execute("SELECT platform, institution FROM metadata")
        async for r in cursor:
            meta_dict[r[0]] = r[1]

        # INCOIS total from metadata
        cursor = await db.execute("SELECT COUNT(*) FROM metadata WHERE institution = 'IN'")
        incois_total = (await cursor.fetchone())[0]

    # Process rows — all floats in the 90-day window are "active" by definition
    active_floats = []
    ocean_counts = {}
    inst_counts = {}
    total_core = 0
    total_bgc = 0
    incois_visible = 0
    incois_core_visible = 0
    incois_bgc_visible = 0

    for row in rows:
        # Skip rows with invalid coordinates (0.01% of data — filtered in Python for speed)
        lat, lon = row['lat'], row['lon']
        if lat is None or lon is None or lat < -90 or lat > 90 or lon < -180 or lon > 180:
            continue
        filename = os.path.basename(row['file'])
        platform, cycle = extract_metadata(filename)
        is_bgc = platform in BGC_PLATFORMS
        inst = meta_dict.get(platform, row['institution'])

        f_data = {
            'platform': platform,
            'cycle': cycle,
            'date': row['date'],
            'lat': row['lat'],
            'lon': row['lon'],
            'institution': inst,
            'ocean': row['ocean'],
            'type': 'bgc' if is_bgc else 'core',
            'status': 'active'  # All floats in the 90-day window are active
        }
        active_floats.append(f_data)

        if is_bgc:
            total_bgc += 1
        else:
            total_core += 1

        if inst == 'IN':
            incois_visible += 1
            if is_bgc:
                incois_bgc_visible += 1
            else:
                incois_core_visible += 1

        o = row['ocean']
        ocean_counts[o] = ocean_counts.get(o, 0) + 1
        inst_counts[inst] = inst_counts.get(inst, 0) + 1

    ocean_labels = {'I': 'Indian', 'P': 'Pacific', 'A': 'Atlantic', '': 'Unknown'}
    active_count = len(active_floats)  # All returned floats are active

    return {
        "count": len(active_floats),
        "active_count": active_count,
        "core_count": total_core,
        "bgc_count": total_bgc,
        "active_core_count": total_core,
        "active_bgc_count": total_bgc,
        "ocean_counts": {ocean_labels.get(k, k): v for k, v in sorted(ocean_counts.items(), key=lambda x: -x[1])},
        "inst_counts": dict(sorted(inst_counts.items(), key=lambda x: -x[1])),
        "incois_total": incois_total,
        "incois_visible": incois_visible,
        "incois_core_visible": incois_core_visible,
        "incois_bgc_visible": incois_bgc_visible,
        "bgc_parameter_counts": BGC_PARAM_COUNTS,
        "floats": active_floats
    }


@app.get("/api/active_floats")
@limiter.limit("30/minute")
async def get_active_floats(request: Request, startDate: Optional[str] = None, endDate: Optional[str] = None):
    """Returns the latest position of floats within the date range.
    Active/inactive is determined by 90 days from the endDate.
    Result is cached for 30 minutes for instant responses."""
    global ACTIVE_FLOATS_CACHE
    now = time.time()
    
    cache_key = f"{startDate}_{endDate}" if startDate or endDate else "global"
    
    if len(ACTIVE_FLOATS_CACHE) > 50:
        # Prevent memory leak from arbitrary date combos
        ACTIVE_FLOATS_CACHE.clear()
        
    cache_entry = ACTIVE_FLOATS_CACHE.get(cache_key)
    
    if not cache_entry or (now - cache_entry['timestamp']) > cache_entry['ttl']:
        response = await _build_active_floats_response(startDate, endDate)
        ACTIVE_FLOATS_CACHE[cache_key] = {
            'response': response,
            'timestamp': now,
            'ttl': 1800
        }
        return response
        
    return cache_entry['response']


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM profiles")
        profile_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM metadata")
        meta_count = (await cursor.fetchone())[0]
    return {
        "status": "ok",
        "profiles": profile_count,
        "metadata": meta_count,
        "bgc_platforms": len(BGC_PLATFORMS),
        "bgc_params": BGC_PARAM_COUNTS,
    }


@app.post("/api/admin/resync")
async def admin_resync():
    """Manual trigger to rebuild SQLite DB from index files. Use when index files are updated."""
    global ACTIVE_FLOATS_CACHE
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('PRAGMA journal_mode=WAL')
        print("Admin resync: rebuilding profiles...")
        await db.execute("DELETE FROM profiles")
        await sync_index_to_db(db)
        print("Admin resync: rebuilding metadata...")
        await db.execute("DELETE FROM metadata")
        await sync_metadata_to_db(db)
    BGC_PLATFORMS.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT platform FROM profiles WHERE type='bio'")
        async for row in cursor:
            BGC_PLATFORMS.add(row[0])
    await load_bgc_parameter_counts()
    ACTIVE_FLOATS_CACHE.clear()
    await precompute_active_floats()
    return {"status": "resynced", "bgc_platforms": len(BGC_PLATFORMS)}

@app.get("/api/trajectory/{platform_id}")
@limiter.limit("20/minute")
async def get_trajectory(platform_id: str, request: Request):
    """Returns all historical positions for a given platform ID using SQLite."""
    query = "SELECT date, lat, lon, file FROM profiles WHERE platform = ? AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180"
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, [platform_id]) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No trajectory data found for platform {platform_id}")

    points = []
    for r in rows:
        _, cycle = extract_metadata(os.path.basename(r['file']))
        points.append({
            'cycle': cycle,
            'date': r['date'],
            'lat': r['lat'],
            'lon': r['lon']
        })

    points.sort(key=lambda x: x['date'])

    return {
        "platform_id": platform_id,
        "total_cycles": len(points),
        "first_date": points[0]['date'],
        "last_date": points[-1]['date'],
        "points": points
    }


# ── Shared Search & Extract Helper ─────────────────────────────────────────────
async def search_and_extract(bounds_dict, params_dict):
    """
    Shared helper: performs SQLite search → download → parallel extraction.
    Caches results in diskcache.
    """
    # Create cache key from sorted params
    cache_key = hashlib.md5(json.dumps({**bounds_dict, **params_dict}, sort_keys=True).encode()).hexdigest()
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data['results'], cached_data['stats']

    params_obj = ParamsObj({
        'minDepth': float(params_dict.get('minDepth', 0)),
        'maxDepth': float(params_dict.get('maxDepth', 2000)),
    })

    # 1. DB Search
    filtered = await db_query_profiles(
        params_dict['startDate'],
        params_dict['endDate'],
        params_dict.get('type', 'core'),
        bounds_dict
    )

    if len(filtered) > MAX_PROFILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Request too large ({len(filtered)} profiles). Max {MAX_PROFILES_PER_REQUEST}.")

    if not filtered:
        return [], {'total': 0, 'extracted': 0, 'errors': 0}

    loop = asyncio.get_event_loop()
    all_results = []
    error_count = 0
    extracted_count = 0

    # Batching for downloads
    for chunk_start in range(0, len(filtered), BATCH_CHUNK_SIZE):
        chunk = filtered[chunk_start:chunk_start + BATCH_CHUNK_SIZE]

        async def download_one(profile):
            try:
                path = await download_with_retry(profile['file'])
                return profile, path
            except Exception as e:
                return profile, e

        download_results = await asyncio.gather(*[download_one(p) for p in chunk])

        # Concurrent Extraction with Semaphore limit
        async def extract_one(profile, local_path):
            nonlocal extracted_count, error_count
            try:
                async with EXTRACTION_SEMAPHORE:
                    extracted = await loop.run_in_executor(
                        PROCESS_POOL, process_netcdf, local_path, params_obj
                    )
                    filename = os.path.basename(profile['file'])
                    platform, cycle = extract_metadata(filename)
                    for row in extracted:
                        row.update({
                            'Date': profile['date'],
                            'Latitude': profile['lat'],
                            'Longitude': profile['lon'],
                            'Platform': platform,
                            'Cycle': cycle,
                            'Institution': profile.get('institution', ''),
                            'Ocean': profile.get('ocean', ''),
                            'File': filename
                        })
                        all_results.append(row)
                    extracted_count += 1
            except Exception:
                error_count += 1

        await asyncio.gather(*[extract_one(p, r) for p, r in download_results if not isinstance(r, Exception)])
        
        # Count download errors
        for _, r in download_results:
            if isinstance(r, Exception):
                error_count += 1

    stats = {
        'total': len(filtered),
        'extracted': extracted_count,
        'errors': error_count
    }
    
    # Cache for 24 hours
    cache.set(cache_key, {'results': all_results, 'stats': stats}, expire=86400)

    return all_results, stats


# ── Multi-Format Export Endpoints ──────────────────────────────────────────────

# ── Async Export Job Runner ────────────────────────────────────────────────────

async def _run_export_job(job_id, fmt, bounds_dict, params_dict, extra_params=None):
    """Background task that runs the export and saves the result to disk."""
    import tempfile
    try:
        EXPORT_JOBS[job_id]['status'] = 'running'
        
        # 1. DB Search to get profile count first
        filtered = await db_query_profiles(
            params_dict['startDate'],
            params_dict['endDate'],
            params_dict.get('type', 'core'),
            bounds_dict
        )
        
        if not filtered:
            EXPORT_JOBS[job_id]['status'] = 'error'
            EXPORT_JOBS[job_id]['error'] = 'No data found for the given search parameters.'
            return
        
        if len(filtered) > MAX_PROFILES_PER_REQUEST:
            EXPORT_JOBS[job_id]['status'] = 'error'
            EXPORT_JOBS[job_id]['error'] = f'Request too large ({len(filtered)} profiles). Max {MAX_PROFILES_PER_REQUEST}.'
            return
        
        EXPORT_JOBS[job_id]['total'] = len(filtered)
        
        # 2. Download and extract with progress tracking
        all_results, stats = await _search_extract_with_progress(job_id, filtered, params_dict)
        
        if not all_results:
            EXPORT_JOBS[job_id]['status'] = 'error'
            EXPORT_JOBS[job_id]['error'] = 'No data could be extracted from the matched profiles.'
            return
        
        # 3. Format the output
        EXPORT_JOBS[job_id]['status'] = 'formatting'
        
        if fmt == 'csv':
            df = pd.DataFrame(all_results)
            df.replace('', np.nan, inplace=True)
            df.dropna(axis=1, how='all', inplace=True)
            df.rename(columns={'depth': 'Depth (dbar)'}, inplace=True)
            
            out_path = os.path.join(DOWNLOADS_DIR, f'{job_id}.csv')
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            with open(out_path, 'w', encoding='utf-8-sig') as f:
                f.write(csv_buffer.getvalue())
            
            EXPORT_JOBS[job_id]['result_path'] = out_path
            EXPORT_JOBS[job_id]['filename'] = f'argo_export_{stats["total"]}_profiles.csv'
            EXPORT_JOBS[job_id]['media_type'] = 'text/csv'
            
        elif fmt == 'json':
            clean_results = []
            for row in all_results:
                clean_row = {}
                for k, v in row.items():
                    if isinstance(v, float) and np.isnan(v):
                        clean_row[k] = None
                    else:
                        clean_row[k] = v
                clean_results.append(clean_row)
            
            output = {
                "metadata": {
                    "total_profiles": stats['total'],
                    "extracted_profiles": stats['extracted'],
                    "total_rows": len(clean_results),
                    "errors": stats['errors'],
                    "search_params": params_dict,
                    "bounds": bounds_dict,
                },
                "data": clean_results
            }
            
            out_path = os.path.join(DOWNLOADS_DIR, f'{job_id}.json')
            with open(out_path, 'w') as f:
                json.dump(output, f, indent=2, default=str)
            
            EXPORT_JOBS[job_id]['result_path'] = out_path
            EXPORT_JOBS[job_id]['filename'] = f'argo_export_{stats["total"]}_profiles.json'
            EXPORT_JOBS[job_id]['media_type'] = 'application/json'
            
        elif fmt == 'netcdf':
            df = pd.DataFrame(all_results)
            df.replace('', np.nan, inplace=True)
            df.dropna(axis=1, how='all', inplace=True)
            for col in df.columns:
                if col not in ['Date', 'Platform', 'Cycle', 'Institution', 'Ocean', 'File']:
                    try:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    except Exception:
                        pass
            ds = xr.Dataset.from_dataframe(df.reset_index(drop=True))
            ds.attrs = {
                'title': 'Argo Nexus Data Export',
                'source': 'Argo Nexus India — IFREMER GDAC',
                'institution': 'INCOIS',
                'total_profiles': stats['total'],
                'extracted_profiles': stats['extracted'],
                'conventions': 'CF-1.8',
            }
            out_path = os.path.join(DOWNLOADS_DIR, f'{job_id}.nc')
            ds.to_netcdf(out_path, engine='scipy')
            
            EXPORT_JOBS[job_id]['result_path'] = out_path
            EXPORT_JOBS[job_id]['filename'] = f'argo_export_{stats["total"]}_profiles.nc'
            EXPORT_JOBS[job_id]['media_type'] = 'application/x-netcdf'
            
        elif fmt == 'diva':
            loop = asyncio.get_event_loop()
            gridded_ds = await loop.run_in_executor(
                PROCESS_POOL,
                grid_argo_data,
                all_results,
                extra_params['variable'],
                bounds_dict,
                extra_params['depth_level'],
                extra_params['depth_tolerance'],
                extra_params['resolution'],
                extra_params['method'],
                extra_params['corr_length'],
                extra_params['snr']
            )
            if gridded_ds is None:
                EXPORT_JOBS[job_id]['status'] = 'error'
                EXPORT_JOBS[job_id]['error'] = f"Insufficient data for DIVA gridding. Need at least 3 observations."
                return
            
            out_path = os.path.join(DOWNLOADS_DIR, f'{job_id}.nc')
            gridded_ds.to_netcdf(out_path, engine='scipy')
            
            EXPORT_JOBS[job_id]['result_path'] = out_path
            EXPORT_JOBS[job_id]['filename'] = f'argo_diva_{extra_params["variable"]}_{extra_params["depth_level"]}m.nc'
            EXPORT_JOBS[job_id]['media_type'] = 'application/x-netcdf'
        
        EXPORT_JOBS[job_id]['status'] = 'done'
        
    except Exception as e:
        EXPORT_JOBS[job_id]['status'] = 'error'
        EXPORT_JOBS[job_id]['error'] = str(e)


async def _search_extract_with_progress(job_id, filtered, params_dict):
    """Download & extract with progress tracking for the job system."""
    params_obj = ParamsObj({
        'minDepth': float(params_dict.get('minDepth', 0)),
        'maxDepth': float(params_dict.get('maxDepth', 2000)),
    })
    
    loop = asyncio.get_event_loop()
    all_results = []
    error_count = 0
    extracted_count = 0

    for chunk_start in range(0, len(filtered), BATCH_CHUNK_SIZE):
        chunk = filtered[chunk_start:chunk_start + BATCH_CHUNK_SIZE]

        async def download_one(profile):
            try:
                path = await download_with_retry(profile['file'])
                return profile, path
            except Exception as e:
                return profile, e

        download_results = await asyncio.gather(*[download_one(p) for p in chunk])

        async def extract_one(profile, local_path):
            nonlocal extracted_count, error_count
            try:
                async with EXTRACTION_SEMAPHORE:
                    extracted = await loop.run_in_executor(
                        PROCESS_POOL, process_netcdf, local_path, params_obj
                    )
                    filename = os.path.basename(profile['file'])
                    platform, cycle = extract_metadata(filename)
                    for row in extracted:
                        row.update({
                            'Date': profile['date'],
                            'Latitude': profile['lat'],
                            'Longitude': profile['lon'],
                            'Platform': platform,
                            'Cycle': cycle,
                            'Institution': profile.get('institution', ''),
                            'Ocean': profile.get('ocean', ''),
                            'File': filename
                        })
                        all_results.append(row)
                    extracted_count += 1
            except Exception:
                error_count += 1

        await asyncio.gather(*[extract_one(p, r) for p, r in download_results if not isinstance(r, Exception)])
        
        for _, r in download_results:
            if isinstance(r, Exception):
                error_count += 1
        
        # Update progress
        EXPORT_JOBS[job_id]['progress'] = min(chunk_start + len(chunk), len(filtered))

    stats = {
        'total': len(filtered),
        'extracted': extracted_count,
        'errors': error_count
    }
    return all_results, stats


# ── Export Job Endpoints ───────────────────────────────────────────────────────

@app.post("/api/export/{fmt}")
async def submit_export(fmt: str, req: ExportRequest):
    """Submit an export job. Returns a job_id immediately for polling."""
    if fmt not in ('csv', 'json', 'netcdf'):
        raise HTTPException(status_code=400, detail=f"Unknown format: {fmt}")
    
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()
    params_dict['selectedVars'] = req.selectedVars or []
    
    # Check cache first for instant response
    cache_key = hashlib.md5(json.dumps({**bounds_dict, **params_dict, 'fmt': fmt}, sort_keys=True).encode()).hexdigest()
    cached_data = cache.get(cache_key)
    if cached_data:
        # Rebuild from cached search results
        pass  # Let the job system handle it; the search_and_extract inside has its own cache
    
    job_id = str(uuid.uuid4())
    EXPORT_JOBS[job_id] = {
        'status': 'queued',
        'progress': 0,
        'total': 0,
        'result_path': None,
        'error': None,
        'format': fmt,
        'filename': None,
        'media_type': None,
    }
    
    # Launch background task
    asyncio.create_task(_run_export_job(job_id, fmt, bounds_dict, params_dict))
    
    return {'job_id': job_id, 'status': 'queued'}


@app.get("/api/export/status/{job_id}")
async def export_status(job_id: str):
    """Poll the status of an export job."""
    job = EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    return {
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'total': job['total'],
        'error': job['error'],
        'filename': job['filename'],
    }


@app.get("/api/export/download/{job_id}")
async def export_download(job_id: str):
    """Download the completed export file."""
    job = EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job['status'] != 'done':
        raise HTTPException(status_code=409, detail=f"Job is still {job['status']}")
    if not job['result_path'] or not os.path.exists(job['result_path']):
        raise HTTPException(status_code=410, detail="Export file no longer available")
    
    def file_stream():
        with open(job['result_path'], 'rb') as f:
            while chunk := f.read(8192):
                yield chunk
    
    return StreamingResponse(
        file_stream(),
        media_type=job['media_type'],
        headers={
            'Content-Disposition': f'attachment; filename="{job["filename"]}"'
        }
    )


# ── DIVA Gridded Export Endpoint ───────────────────────────────────────────────

@app.post("/api/export/diva")
async def export_diva_gridded(req: DivaExportRequest):
    """Submit a DIVA gridded export job. Returns a job_id immediately."""
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()

    extra_params = {
        'variable': req.variable,
        'depth_level': req.depth_level,
        'depth_tolerance': req.depth_tolerance,
        'resolution': req.resolution,
        'method': req.method,
        'corr_length': req.corr_length,
        'snr': req.snr,
    }

    job_id = str(uuid.uuid4())
    EXPORT_JOBS[job_id] = {
        'status': 'queued',
        'progress': 0,
        'total': 0,
        'result_path': None,
        'error': None,
        'format': 'diva',
        'filename': None,
        'media_type': None,
    }

    asyncio.create_task(_run_export_job(job_id, 'diva', bounds_dict, params_dict, extra_params))

    return {'job_id': job_id, 'status': 'queued'}


# ── Gridded Data Product Endpoint ──────────────────────────────────────────────

@app.post("/api/grid")
async def generate_grid(req: GridRequest):
    """
    Generate a gridded data product from Argo profiles using DIVA-style
    Optimal Interpolation.
    """
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for gridding.")

    # Run the gridding in the process pool (CPU-intensive)
    loop = asyncio.get_event_loop()
    gridded_ds = await loop.run_in_executor(
        PROCESS_POOL,
        grid_argo_data,
        all_results,
        req.variable,
        bounds_dict,
        req.depth_level,
        req.depth_tolerance,
        req.resolution,
        req.method,
        req.corr_length,
        req.snr
    )

    if gridded_ds is None:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient data for gridding variable '{req.variable}' at depth {req.depth_level}m. Need at least 3 observations."
        )

    # Convert to JSON-serializable response
    grid_data = gridded_ds[req.variable].values.tolist()
    lats = gridded_ds['lat'].values.tolist()
    lons = gridded_ds['lon'].values.tolist()

    # Replace NaN with null for JSON
    def clean_grid(data):
        if isinstance(data, list):
            return [clean_grid(x) for x in data]
        if isinstance(data, float) and (np.isnan(data) or np.isinf(data)):
            return None
        return data

    return {
        "variable": req.variable,
        "depth_level": req.depth_level,
        "resolution": req.resolution,
        "method": req.method,
        "n_observations": int(gridded_ds.attrs.get('n_observations', 0)),
        "n_profiles": stats['total'],
        "lats": lats,
        "lons": lons,
        "grid": clean_grid(grid_data),
        "bounds": bounds_dict,
        "stats": {
            "min": float(np.nanmin(gridded_ds[req.variable].values)) if not np.all(np.isnan(gridded_ds[req.variable].values)) else None,
            "max": float(np.nanmax(gridded_ds[req.variable].values)) if not np.all(np.isnan(gridded_ds[req.variable].values)) else None,
            "mean": float(np.nanmean(gridded_ds[req.variable].values)) if not np.all(np.isnan(gridded_ds[req.variable].values)) else None,
        }
    }


@app.post("/api/grid/download")
async def download_grid_netcdf(req: GridRequest):
    """
    Generate and download gridded data product as NetCDF file.
    """
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for gridding.")

    loop = asyncio.get_event_loop()
    gridded_ds = await loop.run_in_executor(
        PROCESS_POOL,
        grid_argo_data,
        all_results,
        req.variable,
        bounds_dict,
        req.depth_level,
        req.depth_tolerance,
        req.resolution,
        req.method,
        req.corr_length,
        req.snr
    )

    if gridded_ds is None:
        raise HTTPException(status_code=422, detail="Insufficient data for gridding.")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
        tmp_path = tmp.name
    gridded_ds.to_netcdf(tmp_path, engine='scipy')
    with open(tmp_path, 'rb') as f:
        nc_bytes = f.read()
    os.remove(tmp_path)

    return Response(
        content=nc_bytes,
        media_type='application/x-netcdf',
        headers={
            'Content-Disposition': f'attachment; filename="argo_gridded_{req.variable}_{req.depth_level}m_{req.resolution}deg.nc"'
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
