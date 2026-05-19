import os
import asyncio
import aiosqlite
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime
import multiprocessing

# We reuse existing logic where possible, but this script is meant to run standalone.
# To use process_netcdf and other helpers, we import from main
from main import download_with_retry, process_netcdf, ParamsObj, extract_metadata

DB_PATH = 'argo_index.db'
PARQUET_BASE_DIR = 'data/parquet'
PROCESS_POOL = multiprocessing.Pool(processes=max(1, multiprocessing.cpu_count() - 1))
BATCH_SIZE = 100 # Process 100 files at a time

async def get_all_profiles():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} not found. Run the main server first to initialize the index.")
        return []
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Fetching a small limit for demonstration. Remove 'LIMIT 1000' in true production
        async with db.execute("SELECT * FROM profiles LIMIT 1000") as cursor:
            return [dict(row) for row in await cursor.fetchall()]

def process_and_append_parquet(batch_df, ocean, year):
    """
    Appends a DataFrame to a partitioned Parquet file.
    In a real massive pipeline, you'd use something like Apache Beam or Spark, 
    but for a local pipeline we append to PyArrow tables.
    """
    # Sanitize ocean string (remove spaces, etc.)
    ocean_safe = ocean.strip().replace(' ', '_').lower() if ocean else 'unknown'
    year_str = str(year)
    
    partition_dir = os.path.join(PARQUET_BASE_DIR, f"ocean={ocean_safe}", f"year={year_str}")
    os.makedirs(partition_dir, exist_ok=True)
    file_path = os.path.join(partition_dir, "data.parquet")
    
    table = pa.Table.from_pandas(batch_df)
    
    if os.path.exists(file_path):
        # Append to existing
        existing_table = pq.read_table(file_path)
        combined_table = pa.concat_tables([existing_table, table])
        pq.write_table(combined_table, file_path, compression='SNAPPY')
    else:
        pq.write_table(table, file_path, compression='SNAPPY')

async def process_batch(chunk, loop):
    async def download_one(profile):
        try:
            path = await download_with_retry(profile['file'])
            return profile, path
        except Exception as e:
            print(f"Failed to download {profile['file']}: {e}")
            return profile, e

    download_results = await asyncio.gather(*[download_one(p) for p in chunk])
    
    all_rows = []
    
    # We want ALL available variables here, no filtering
    params_obj = ParamsObj({
        'minDepth': 0.0,
        'maxDepth': 10000.0,
    })
    
    for profile, result in download_results:
        if isinstance(result, Exception):
            continue
            
        local_path = result
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
                all_rows.append(row)
        except Exception as e:
            print(f"Error processing {profile['file']}: {e}")

    if all_rows:
        df = pd.DataFrame(all_rows)
        # Parse date to extract year
        df['Date_obj'] = pd.to_datetime(df['Date'], format='%Y%m%d%H%M%S', errors='coerce')
        df['Year'] = df['Date_obj'].dt.year.fillna(9999).astype(int)
        
        # Group by Ocean and Year and save to partitions
        for (ocean, year), group in df.groupby(['Ocean', 'Year']):
            # Clean up the extra Date_obj before saving
            clean_group = group.drop(columns=['Date_obj', 'Year']).copy()
            # Convert objects to string to ensure parquet compatibility
            for col in clean_group.select_dtypes(include=['object']):
                clean_group[col] = clean_group[col].astype(str)
            process_and_append_parquet(clean_group, ocean, year)
            
    return len(all_rows)


async def main():
    print("Fetching profile metadata from SQLite...")
    profiles = await get_all_profiles()
    print(f"Found {len(profiles)} profiles.")
    
    if not profiles:
        return
        
    print(f"Starting Parquet conversion pipeline... Data will be saved to {PARQUET_BASE_DIR}")
    
    loop = asyncio.get_event_loop()
    total_processed = 0
    total_rows = 0
    
    for chunk_start in range(0, len(profiles), BATCH_SIZE):
        chunk_end = min(chunk_start + BATCH_SIZE, len(profiles))
        chunk = profiles[chunk_start:chunk_end]
        
        print(f"Processing chunk {chunk_start} to {chunk_end}...")
        rows_extracted = await process_batch(chunk, loop)
        total_processed += len(chunk)
        total_rows += rows_extracted
        
        print(f"Progress: {total_processed}/{len(profiles)} profiles | Rows extracted: {total_rows}")

    print("Parquet conversion complete!")

if __name__ == "__main__":
    asyncio.run(main())
