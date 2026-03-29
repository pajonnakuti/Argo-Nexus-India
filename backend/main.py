from fastapi import FastAPI, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
LOCAL_INDEX_PATH = 'ar_index_global_prof.txt'
REMOTE_INDEX_URL = 'https://data-argo.ifremer.fr/ar_index_global_prof.txt'
DOWNLOADS_DIR = 'downloads'
BIO_INDEX_PATH = 'argo_bio-profile_index.txt'
# Ensure downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

#Cached Core and Bio Profiles
CACHED_PROFILES_BIO = []
DATE_SORTED_PROFILES_BIO = []
CACHED_PROFILES_CORE = []
DATE_SORTED_PROFILES_CORE = []
BGC_PLATFORMS = set()

# ── Shared HTTP client & Process Pool ─────────────────────────────────────────
HTTP_CLIENT: httpx.AsyncClient = None
PROCESS_POOL = ProcessPoolExecutor(max_workers=6)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(48)
BATCH_CHUNK_SIZE = 200  # profiles per processing chunk
MAX_DOWNLOAD_RETRIES = 3


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
async def load_bio_index():
    "loads the index file from the bio link and sorts it for binary search"
    global CACHED_PROFILES_BIO, DATE_SORTED_PROFILES_BIO

    if CACHED_PROFILES_BIO:
        return
    # Check if local file exists and is less than 24 hours old
    is_fresh = False
    if os.path.exists(BIO_INDEX_PATH):
        if (time.time() - os.path.getmtime(BIO_INDEX_PATH)) < 86400:
            is_fresh = True
            
    content = ""
    if is_fresh:
        with open(BIO_INDEX_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        print(f"Downloading completely fresh {BIO_INDEX_PATH}...")
        async with httpx.AsyncClient() as client:
            resp = await client.get('https://data-argo.ifremer.fr/argo_bio-profile_index.txt', timeout=600.0)
            content = resp.text
            with open(BIO_INDEX_PATH, 'w', encoding='utf-8') as f:
                f.write(content)

    # Parse the bio index content (10-column format: file,date,lat,lon,ocean,profiler_type,institution,parameters,parameter_data_mode,date_update)
    lines = [line for line in content.splitlines() if not line.startswith('#') and 'file,' not in line]

    data = []
    for line in lines:
        parts = line.split(',')
        if len(parts) >= 7:
            try:
                data.append({
                    'file': parts[0],
                    'date': parts[1],
                    'lat': float(parts[2]),
                    'lon': float(parts[3]),
                    'ocean': parts[4],
                    'profiler_type': parts[5],
                    'institution': parts[6],
                    'date_update': parts[-1] if len(parts) >= 10 else parts[7] if len(parts) >= 8 else ''
                })
            except ValueError:
                continue

    CACHED_PROFILES_BIO = data
    DATE_SORTED_PROFILES_BIO = sorted(data, key=lambda x: x['date'])
    
    # Store BGC platforms for quick lookup
    global BGC_PLATFORMS
    for prof in data:
        filename = os.path.basename(prof['file'])
        match = re.search(r'([A-Z]*)([0-9]+)_([0-9]+D?)', filename)
        if match:
            BGC_PLATFORMS.add(match.group(2))
            
    print(f'Loaded {len(CACHED_PROFILES_BIO)} bio profiles')

async def load_index():
    """Loads the core index file into memory and sorts it for binary search."""
    global CACHED_PROFILES_CORE, DATE_SORTED_PROFILES_CORE

    if CACHED_PROFILES_CORE:
        return

    # Check if local file exists and is less than 24 hours old
    is_fresh = False
    if os.path.exists(LOCAL_INDEX_PATH):
        if (time.time() - os.path.getmtime(LOCAL_INDEX_PATH)) < 86400:
            is_fresh = True

    content = ""
    if is_fresh:
        with open(LOCAL_INDEX_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        print(f"Downloading completely fresh {LOCAL_INDEX_PATH}...")
        async with httpx.AsyncClient() as client:
            resp = await client.get(REMOTE_INDEX_URL, timeout=600.0)
            content = resp.text
            with open(LOCAL_INDEX_PATH, 'w', encoding='utf-8') as f:
                f.write(content)

    lines = [line for line in content.splitlines() if not line.startswith('#') and 'file,' not in line]

    data = []

    for line in lines:
        parts = line.split(',')

        if len(parts) >= 8:
            try:
                data.append({
                    'file': parts[0],
                    'date': parts[1],
                    'lat': float(parts[2]),
                    'lon': float(parts[3]),
                    'ocean': parts[4],
                    'profiler_type': parts[5],
                    'institution': parts[6],
                    'date_update': parts[7]
                })
            except ValueError:
                continue

    CACHED_PROFILES_CORE = data
    DATE_SORTED_PROFILES_CORE = sorted(data, key=lambda x: x['date'])
from datetime import datetime, timedelta

@app.on_event("startup")
async def startup_event():
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
        timeout=httpx.Timeout(120.0, connect=30.0),
    )
    await load_index()
    await load_bio_index()

@app.on_event("shutdown")
async def shutdown_event():
    global HTTP_CLIENT
    if HTTP_CLIENT:
        await HTTP_CLIENT.aclose()
    PROCESS_POOL.shutdown(wait=False)

def binary_search_date_range(start_date, end_date, dataset='core'):

    start_str = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d") + "000000"
    end_str = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d") + "235959"

    if dataset == 'bio':
        profiles = DATE_SORTED_PROFILES_BIO
    else:
        profiles = DATE_SORTED_PROFILES_CORE

    dates = [x['date'] for x in profiles]

    left_idx = bisect.bisect_left(dates, start_str)
    right_idx = bisect.bisect_right(dates, end_str)

    return profiles[left_idx:right_idx]


async def download_with_retry(file_path):
    """Download a NetCDF file with retry and exponential backoff using the shared client."""
    url = f"https://data-argo.ifremer.fr/dac/{file_path}"
    filename = os.path.basename(file_path)
    local_path = os.path.join(DOWNLOADS_DIR, filename)

    if os.path.exists(local_path):
        return local_path

    for attempt in range(MAX_DOWNLOAD_RETRIES):
        try:
            async with DOWNLOAD_SEMAPHORE:
                resp = await HTTP_CLIENT.get(url)
                resp.raise_for_status()
                with open(local_path, 'wb') as f:
                    f.write(resp.content)
                return local_path
        except Exception as e:
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
            else:
                raise e
    return local_path  # unreachable but satisfies linter

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
        
        # 1. Date Filter
        candidates = binary_search_date_range(
            params['startDate'], 
            params['endDate'],
            params['type'])

        await websocket.send_json({"type": "log", "message": f"Found {len(candidates)} profiles in date range."})
        
        # 2. Geo Filter
        print(f"[DEBUG] Bounds: N={bounds['north']:.4f} S={bounds['south']:.4f} E={bounds['east']:.4f} W={bounds['west']:.4f}  type={params['type']}")

        def geo_filter(candidates, b):
            return [
                p for p in candidates
                if b['south'] <= p['lat'] <= b['north'] and
                   b['west'] <= p['lon'] <= b['east']
            ]

        filtered = geo_filter(candidates, bounds)

        # Fallback: if longitudes were negative (map drew in wrong hemisphere),
        # retry with absolute values so Indian Ocean (e.g. 64-68E) works when
        # map accidentally returns negative longitudes (-68 to -64).
        if not filtered and (bounds['east'] <= 0 or bounds['west'] <= 0):
            abs_bounds = {
                'north': bounds['north'],
                'south': bounds['south'],
                'east':  max(abs(bounds['east']), abs(bounds['west'])),
                'west':  min(abs(bounds['east']), abs(bounds['west'])),
            }
            filtered = geo_filter(candidates, abs_bounds)
            if filtered:
                await websocket.send_json({"type": "log", "message": f"[Auto-corrected] Longitude sign fixed: W={abs_bounds['west']:.2f}\u00b0E to E={abs_bounds['east']:.2f}\u00b0E. Found {len(filtered)} profiles."})
                print(f"[DEBUG] Abs-lon fallback found {len(filtered)} profiles")

        await websocket.send_json({"type": "log", "message": f"Geographic filter reduced to {len(filtered)} profiles."})

        if not filtered:
            await websocket.send_json({"type": "error", "message": "No profiles found in selected area/date range. Please check your bounding box covers the correct ocean region."})
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
async def get_active_floats():
    """Returns the latest location of all active floats within the last 30 days.
    Scans both Core and Bio indices. Includes ocean and institution metadata."""
    if not DATE_SORTED_PROFILES_CORE and not DATE_SORTED_PROFILES_BIO:
        return {"error": "Index not loaded yet."}

    now = datetime.utcnow()
    thirty_days_ago = (now - timedelta(days=30)).strftime("%Y%m%d%H%M%S")

    active_floats = {}
    
    # Scan Core index
    for profile in reversed(DATE_SORTED_PROFILES_CORE):
        if profile['date'] < thirty_days_ago:
            break
            
        filename = os.path.basename(profile['file'])
        platform, cycle = extract_metadata(filename)
        
        if platform and platform not in active_floats:
            is_bgc = platform in BGC_PLATFORMS
            active_floats[platform] = {
                'platform': platform,
                'cycle': cycle,
                'date': profile['date'],
                'lat': profile['lat'],
                'lon': profile['lon'],
                'institution': profile.get('institution', ''),
                'ocean': profile.get('ocean', ''),
                'type': 'bgc' if is_bgc else 'core',
            }

    # Scan Bio index to catch BGC-only floats not in core index
    for profile in reversed(DATE_SORTED_PROFILES_BIO):
        if profile['date'] < thirty_days_ago:
            break

        filename = os.path.basename(profile['file'])
        platform, cycle = extract_metadata(filename)

        if platform and platform not in active_floats:
            active_floats[platform] = {
                'platform': platform,
                'cycle': cycle,
                'date': profile['date'],
                'lat': profile['lat'],
                'lon': profile['lon'],
                'institution': profile.get('institution', ''),
                'ocean': profile.get('ocean', ''),
                'type': 'bgc',
            }
        elif platform and platform in active_floats:
            # Mark existing core float as BGC if it also appears in bio index
            active_floats[platform]['type'] = 'bgc'

    count_core = sum(1 for f in active_floats.values() if f['type'] == 'core')
    count_bgc = sum(1 for f in active_floats.values() if f['type'] == 'bgc')

    # Build ocean and institution breakdowns
    ocean_counts = {}
    inst_counts = {}
    for f in active_floats.values():
        o = f.get('ocean', '')
        i = f.get('institution', '')
        ocean_counts[o] = ocean_counts.get(o, 0) + 1
        inst_counts[i] = inst_counts.get(i, 0) + 1

    # Map ocean codes to readable names
    ocean_labels = {'I': 'Indian', 'P': 'Pacific', 'A': 'Atlantic', '': 'Unknown'}

    return {
        "count": len(active_floats), 
        "core_count": count_core,
        "bgc_count": count_bgc,
        "ocean_counts": {ocean_labels.get(k, k): v for k, v in sorted(ocean_counts.items(), key=lambda x: -x[1])},
        "inst_counts": dict(sorted(inst_counts.items(), key=lambda x: -x[1])),
        "floats": list(active_floats.values())
    }

@app.get("/api/trajectory/{platform_id}")
async def get_trajectory(platform_id: str):
    """
    Returns all historical positions for a given platform (float) ID,
    sorted by date. Scans the in-memory index — no downloads needed.
    """
    if not CACHED_PROFILES_CORE and not CACHED_PROFILES_BIO:
        raise HTTPException(status_code=503, detail="Index not loaded yet.")

    points = {}  # key: cycle, value: best profile entry

    # Helper to extract platform + cycle from a file path string
    def get_platform_cycle(file_path):
        filename = os.path.basename(file_path)
        match = re.search(r'[A-Z]*([0-9]+)_([0-9]+D?)', filename)
        if match:
            return match.group(1), match.group(2)
        return None, None

    for profile in CACHED_PROFILES_CORE:
        plat, cycle = get_platform_cycle(profile['file'])
        if plat == platform_id:
            # Keep the most recent update for each cycle
            if cycle not in points or profile['date'] > points[cycle]['date']:
                points[cycle] = {
                    'cycle': cycle,
                    'date': profile['date'],
                    'lat': profile['lat'],
                    'lon': profile['lon'],
                }

    for profile in CACHED_PROFILES_BIO:
        plat, cycle = get_platform_cycle(profile['file'])
        if plat == platform_id:
            if cycle not in points or profile['date'] > points[cycle]['date']:
                points[cycle] = {
                    'cycle': cycle,
                    'date': profile['date'],
                    'lat': profile['lat'],
                    'lon': profile['lon'],
                }

    if not points:
        raise HTTPException(status_code=404, detail=f"No trajectory data found for platform {platform_id}")

    # Sort by date ascending
    sorted_points = sorted(points.values(), key=lambda x: x['date'])

    return {
        "platform_id": platform_id,
        "total_cycles": len(sorted_points),
        "first_date": sorted_points[0]['date'],
        "last_date": sorted_points[-1]['date'],
        "points": sorted_points
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
