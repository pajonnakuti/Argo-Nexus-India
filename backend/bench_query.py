"""Benchmark different query strategies for the active floats query."""
import sqlite3
import time
from datetime import datetime, timedelta

conn = sqlite3.connect('argo_index.db')
c = conn.cursor()

end_dt = datetime(2026, 5, 15)
end_str = end_dt.strftime("%Y%m%d") + "235959"
start_dt = end_dt - timedelta(days=90)
start_str = start_dt.strftime("%Y%m%d") + "000000"

print(f"Date window: {start_str} to {end_str}\n")

# Strategy 1: Current self-join query (SLOW)
t0 = time.time()
c.execute("""
    SELECT p.file, p.platform, p.date, p.lat, p.lon, p.ocean, 
           p.profiler_type, p.institution, p.type
    FROM profiles p
    INNER JOIN (
        SELECT platform, MAX(date) as max_date
        FROM profiles
        WHERE date BETWEEN ? AND ?
        AND lat BETWEEN -90 AND 90
        AND lon BETWEEN -180 AND 180
        GROUP BY platform
    ) latest ON p.platform = latest.platform AND p.date = latest.max_date
    WHERE p.lat BETWEEN -90 AND 90
    AND p.lon BETWEEN -180 AND 180
    GROUP BY p.platform
""", [start_str, end_str])
rows1 = c.fetchall()
t1 = time.time()
print(f"Strategy 1 (self-join):     {len(rows1)} platforms in {t1-t0:.2f}s")

# Strategy 2: Simple GROUP BY with MAX(date) - SQLite bare column behavior
t0 = time.time()
c.execute("""
    SELECT file, platform, MAX(date) as date, lat, lon, ocean, 
           profiler_type, institution, type
    FROM profiles
    WHERE date BETWEEN ? AND ?
    AND lat BETWEEN -90 AND 90
    AND lon BETWEEN -180 AND 180
    GROUP BY platform
""", [start_str, end_str])
rows2 = c.fetchall()
t2 = time.time()
print(f"Strategy 2 (GROUP BY MAX): {len(rows2)} platforms in {t2-t0:.2f}s")

# Strategy 3: With a better index — create it first
c.execute("CREATE INDEX IF NOT EXISTS idx_date_plat ON profiles(date, platform)")
conn.commit()

t0 = time.time()
c.execute("""
    SELECT file, platform, MAX(date) as date, lat, lon, ocean, 
           profiler_type, institution, type
    FROM profiles
    WHERE date BETWEEN ? AND ?
    AND lat BETWEEN -90 AND 90
    AND lon BETWEEN -180 AND 180
    GROUP BY platform
""", [start_str, end_str])
rows3 = c.fetchall()
t3 = time.time()
print(f"Strategy 3 (+ index):      {len(rows3)} platforms in {t3-t0:.2f}s")

# Verify all strategies return same count
print(f"\nAll same count? {len(rows1)} == {len(rows2)} == {len(rows3)}: {len(rows1) == len(rows2) == len(rows3)}")

# Strategy 4: Remove the lat/lon WHERE clause (almost all rows are valid)
t0 = time.time()
c.execute("""
    SELECT file, platform, MAX(date) as date, lat, lon, ocean, 
           profiler_type, institution, type
    FROM profiles
    WHERE date BETWEEN ? AND ?
    GROUP BY platform
""", [start_str, end_str])
rows4 = c.fetchall()
t4 = time.time()
print(f"Strategy 4 (no geo filter): {len(rows4)} platforms in {t4-t0:.2f}s")

# Check how many rows have invalid lat/lon
c.execute("SELECT COUNT(*) FROM profiles WHERE lat NOT BETWEEN -90 AND 90 OR lon NOT BETWEEN -180 AND 180")
invalid = c.fetchone()[0]
print(f"\nRows with invalid lat/lon: {invalid} out of 3.7M ({invalid/3713486*100:.2f}%)")

conn.close()
