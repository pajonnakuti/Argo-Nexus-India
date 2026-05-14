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

        # Check if we need to sync profiles
        cursor = await db.execute('SELECT COUNT(*) FROM profiles')
        count = (await cursor.fetchone())[0]
        
        needs_sync = count == 0
        if not needs_sync:
            for path in [LOCAL_INDEX_PATH, BIO_INDEX_PATH]:
                if os.path.exists(path) and os.path.getmtime(path) > os.path.getmtime(DB_PATH):
                    needs_sync = True
                    break
        
        if needs_sync:
            print("Syncing SQLite DB from index files...")
            await sync_index_to_db(db)

        # Check if we need to sync metadata
        cursor = await db.execute('SELECT COUNT(*) FROM metadata')
        meta_count = (await cursor.fetchone())[0]
        
        needs_meta_sync = meta_count == 0
        if not needs_meta_sync and os.path.exists(META_INDEX_PATH):
            if os.path.getmtime(META_INDEX_PATH) > os.path.getmtime(DB_PATH):
                needs_meta_sync = True
        
        if needs_meta_sync:
            print("Syncing metadata from ar_index_global_meta.txt...")
            await sync_metadata_to_db(db)
            
        # Load BGC platforms into memory for quick lookup
        cursor = await db.execute("SELECT file FROM profiles WHERE type='bio'")
        async for row in cursor:
            filename = os.path.basename(row[0])
            match = re.search(r'([A-Z]*)([0-9]+)_([0-9]+D?)', filename)
            if match:
                BGC_PLATFORMS.add(match.group(2))
        print(f"Loaded {len(BGC_PLATFORMS)} BGC platforms")
        
        # Log metadata counts for verification
        cursor = await db.execute("SELECT COUNT(*) FROM metadata")
        total_meta = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM metadata WHERE institution = 'IN'")
        incois_meta = (await cursor.fetchone())[0]
        print(f"Metadata: {total_meta} total floats, {incois_meta} INCOIS floats")

async def sync_index_to_db(db):
    """Parses .txt files and inserts into SQLite."""
    await ensure_index_files()
    
    for ptype, path in [('core', LOCAL_INDEX_PATH), ('bio', BIO_INDEX_PATH)]:
        print(f"Parsing {ptype} index...")
        async with aiofiles.open(path, mode='r', encoding='utf-8') as f:
            batch = []
            async for line in f:
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
    async with aiofiles.open(META_INDEX_PATH, mode='r', encoding='utf-8') as f:
        async for line in f:
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
MAX_PROFILES_PER_REQUEST = 5000
BATCH_CHUNK_SIZE = 100  # Smaller chunks for better streaming
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

@app.get("/api/active_floats")
@limiter.limit("30/minute")
async def get_active_floats(request: Request, startDate: Optional[str] = None, endDate: Optional[str] = None):
    """Returns the latest location of all floats, colored by active/inactive based on 90 days."""
    
    if not endDate:
        end_dt = datetime.utcnow()
    else:
        end_dt = datetime.strptime(endDate, "%Y-%m-%d")
        
    end_str = end_dt.strftime("%Y%m%d") + "235959"
    
    if not startDate:
        start_str = "19900101000000"
    else:
        start_dt = datetime.strptime(startDate, "%Y-%m-%d")
        start_str = start_dt.strftime("%Y%m%d") + "000000"

    ninety_days_ago = (end_dt - timedelta(days=90)).strftime("%Y%m%d%H%M%S")

    # Optimized query leveraging the new platform column and compound index
    query = '''
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY platform ORDER BY date DESC) as rn
            FROM profiles 
            WHERE date BETWEEN ? AND ?
            AND lat BETWEEN -90 AND 90 
            AND lon BETWEEN -180 AND 180
        ) WHERE rn = 1
    '''
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, [start_str, end_str]) as cursor:
            rows = await cursor.fetchall()
            
    # Fetch all metadata into a dict for authoritative institution mapping
    meta_dict = {}
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT platform, institution FROM metadata")
        async for r in cursor:
            meta_dict[r[0]] = r[1]
            
    active_floats = []
    ocean_counts = {}
    inst_counts = {}
    core_count = 0
    bgc_count = 0
    
    for row in rows:
        filename = os.path.basename(row['file'])
        platform, cycle = extract_metadata(filename)
        is_bgc = platform in BGC_PLATFORMS
        
        # Use authoritative institution from metadata, fallback to profile index
        inst = meta_dict.get(platform, row['institution'])
        
        status = 'active' if row['date'] >= ninety_days_ago else 'inactive'
        
        f_data = {
            'platform': platform,
            'cycle': cycle,
            'date': row['date'],
            'lat': row['lat'],
            'lon': row['lon'],
            'institution': inst,
            'ocean': row['ocean'],
            'type': 'bgc' if is_bgc else 'core',
            'status': status
        }
        active_floats.append(f_data)
        
        if is_bgc: bgc_count += 1
        else: core_count += 1
        
        o = row['ocean']
        i = inst
        ocean_counts[o] = ocean_counts.get(o, 0) + 1
        inst_counts[i] = inst_counts.get(i, 0) + 1

    ocean_labels = {'I': 'Indian', 'P': 'Pacific', 'A': 'Atlantic', '': 'Unknown'}

    # Get authoritative INCOIS float count from metadata table
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM metadata WHERE institution = 'IN'")
        incois_total = (await cursor.fetchone())[0]
    
    # Also count how many of the currently visible floats are INCOIS
    # Cross-reference platform IDs against the metadata table
    platform_ids = list(set(f['platform'] for f in active_floats))
    incois_visible = 0
    if platform_ids:
        async with aiosqlite.connect(DB_PATH) as db:
            # Query in batches to avoid SQL parameter limits
            for i in range(0, len(platform_ids), 500):
                chunk = platform_ids[i:i+500]
                placeholders = ','.join('?' * len(chunk))
                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM metadata WHERE institution = 'IN' AND platform IN ({placeholders})",
                    chunk
                )
                incois_visible += (await cursor.fetchone())[0]

    return {
        "count": len(active_floats), 
        "core_count": core_count,
        "bgc_count": bgc_count,
        "ocean_counts": {ocean_labels.get(k, k): v for k, v in sorted(ocean_counts.items(), key=lambda x: -x[1])},
        "inst_counts": dict(sorted(inst_counts.items(), key=lambda x: -x[1])),
        "incois_total": incois_total,
        "incois_visible": incois_visible,
        "floats": active_floats
    }

@app.get("/api/trajectory/{platform_id}")
@limiter.limit("20/minute")
async def get_trajectory(platform_id: str, request: Request):
    """Returns all historical positions for a given platform ID using SQLite."""
    query = "SELECT date, lat, lon, file FROM profiles WHERE file LIKE ? AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180"
    pattern = f"%/{platform_id}/profiles/%"
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, [pattern]) as cursor:
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

@app.post("/api/export/csv")
async def export_csv(req: ExportRequest):
    """Export search results as CSV."""
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()
    params_dict['selectedVars'] = req.selectedVars or []

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for the given search parameters.")

    df = pd.DataFrame(all_results)
    df.replace('', np.nan, inplace=True)
    df.dropna(axis=1, how='all', inplace=True)
    df.rename(columns={'depth': 'Depth (dbar)'}, inplace=True)

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_content = csv_buffer.getvalue()

    return Response(
        content="\ufeff" + csv_content,
        media_type='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="argo_export_{stats["total"]}_profiles.csv"'
        }
    )


@app.post("/api/export/json")
async def export_json(req: ExportRequest):
    """Export search results as JSON."""
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()
    params_dict['selectedVars'] = req.selectedVars or []

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for the given search parameters.")

    # Clean up NaN values for JSON serialization
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

    json_content = json.dumps(output, indent=2, default=str)

    return Response(
        content=json_content,
        media_type='application/json',
        headers={
            'Content-Disposition': f'attachment; filename="argo_export_{stats["total"]}_profiles.json"'
        }
    )


@app.post("/api/export/netcdf")
async def export_netcdf(req: ExportRequest):
    """Export search results as NetCDF."""
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()
    params_dict['selectedVars'] = req.selectedVars or []

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for the given search parameters.")

    df = pd.DataFrame(all_results)
    df.replace('', np.nan, inplace=True)
    df.dropna(axis=1, how='all', inplace=True)

    # Convert string columns that should be numeric
    for col in df.columns:
        if col not in ['Date', 'Platform', 'Cycle', 'Institution', 'Ocean', 'File']:
            try:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            except Exception:
                pass

    # Build xarray Dataset from the DataFrame
    ds = xr.Dataset.from_dataframe(df.reset_index(drop=True))

    # Add global attributes
    ds.attrs = {
        'title': 'Argo Nexus Data Export',
        'source': 'Argo Nexus India — IFREMER GDAC',
        'institution': 'INCOIS',
        'total_profiles': stats['total'],
        'extracted_profiles': stats['extracted'],
        'conventions': 'CF-1.8',
    }

    # Write to temp file (scipy engine closes BytesIO buffers)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
        tmp_path = tmp.name
    ds.to_netcdf(tmp_path, engine='scipy')
    with open(tmp_path, 'rb') as f:
        nc_bytes = f.read()
    os.remove(tmp_path)

    return Response(
        content=nc_bytes,
        media_type='application/x-netcdf',
        headers={
            'Content-Disposition': f'attachment; filename="argo_export_{stats["total"]}_profiles.nc"'
        }
    )


# ── DIVA Gridded Export Endpoint ───────────────────────────────────────────────

@app.post("/api/export/diva")
async def export_diva_gridded(req: DivaExportRequest):
    """
    Export DIVA-style gridded product as a NetCDF file.
    Runs Optimal Interpolation on the selected Argo profiles and returns
    a CF-compliant NetCDF with the analysis field and DIVA error field.
    """
    bounds_dict = req.bounds.dict()
    params_dict = req.params.dict()

    all_results, stats = await search_and_extract(bounds_dict, params_dict)
    if not all_results:
        raise HTTPException(status_code=404, detail="No data found for DIVA gridding.")

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
            detail=f"Insufficient data for DIVA gridding variable '{req.variable}' at depth {req.depth_level}m. Need at least 3 observations."
        )

    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
        tmp_path = tmp.name
    gridded_ds.to_netcdf(tmp_path, engine='scipy')
    with open(tmp_path, 'rb') as f:
        nc_bytes = f.read()
    os.remove(tmp_path)

    filename = f"argo_diva_{req.variable}_{req.depth_level}m_{req.resolution}deg_{req.method}.nc"

    return Response(
        content=nc_bytes,
        media_type='application/x-netcdf',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    )


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
