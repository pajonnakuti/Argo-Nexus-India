import sqlite3
conn = sqlite3.connect('argo_index.db')
c = conn.cursor()

c.execute('SELECT COUNT(DISTINCT platform) FROM profiles')
print('Total distinct platforms:', c.fetchone()[0])

c.execute('SELECT type, COUNT(DISTINCT platform) FROM profiles GROUP BY type')
print('By type:', c.fetchall())

c.execute("SELECT COUNT(DISTINCT platform) FROM profiles WHERE date >= '20260214000000'")
print('Active (90d from 2026-05-15):', c.fetchone()[0])

c.execute("SELECT COUNT(DISTINCT platform) FROM profiles WHERE date >= '20260515000000' AND date <= '20260515235959'")
print('Profiles with date=today (2026-05-15):', c.fetchone()[0])

c.execute('SELECT COUNT(*) FROM profiles')
print('Total profile rows:', c.fetchone()[0])

c.execute('SELECT COUNT(*) FROM metadata')
print('Metadata entries:', c.fetchone()[0])

# Check the query used by _build_active_floats_response with today's date
c.execute("""
    SELECT COUNT(*) FROM (
        SELECT platform, MAX(date) as max_date
        FROM profiles
        WHERE date BETWEEN '20260515000000' AND '20260515235959'
        AND lat BETWEEN -90 AND 90
        AND lon BETWEEN -180 AND 180
        GROUP BY platform
    )
""")
print('Platforms with profiles on 2026-05-15:', c.fetchone()[0])

# Check what dates look like
c.execute("SELECT MIN(date), MAX(date) FROM profiles")
row = c.fetchone()
print('Date range in DB:', row[0], 'to', row[1])

# Check sample dates for today
c.execute("SELECT date FROM profiles WHERE date LIKE '20260515%' LIMIT 5")
print('Sample dates for today:', c.fetchall())

c.execute("SELECT date FROM profiles WHERE date LIKE '202605%' LIMIT 5")
print('Sample dates for May 2026:', c.fetchall())

# Check what happens with no date filter (the global query)
c.execute("""
    SELECT COUNT(*) FROM (
        SELECT platform, MAX(date) as max_date
        FROM profiles
        WHERE date BETWEEN '19900101000000' AND '20260515235959'
        AND lat BETWEEN -90 AND 90
        AND lon BETWEEN -180 AND 180
        GROUP BY platform
    )
""")
print('Total platforms (global, no date filter):', c.fetchone()[0])

# Check INCOIS floats
c.execute("SELECT COUNT(*) FROM metadata WHERE institution = 'IN'")
print('INCOIS metadata entries:', c.fetchone()[0])

# Check how many INCOIS platforms have profiles
c.execute("""
    SELECT COUNT(DISTINCT p.platform) 
    FROM profiles p 
    JOIN metadata m ON p.platform = m.platform 
    WHERE m.institution = 'IN'
""")
print('INCOIS platforms with profiles:', c.fetchone()[0])

# Check INCOIS platforms with recent profiles (today)
c.execute("""
    SELECT COUNT(DISTINCT p.platform) 
    FROM profiles p 
    JOIN metadata m ON p.platform = m.platform 
    WHERE m.institution = 'IN'
    AND p.date BETWEEN '20260515000000' AND '20260515235959'
""")
print('INCOIS platforms with profiles today:', c.fetchone()[0])

# Check INCOIS platforms active (90 days)
c.execute("""
    SELECT COUNT(DISTINCT p.platform) 
    FROM profiles p 
    JOIN metadata m ON p.platform = m.platform 
    WHERE m.institution = 'IN'
    AND p.date >= '20260214000000'
""")
print('INCOIS platforms active (last 90 days):', c.fetchone()[0])

# Check what latest dates look like
c.execute("SELECT date FROM profiles ORDER BY date DESC LIMIT 10")
print('Latest 10 dates:', c.fetchall())

# Check if core and bio overlap on platforms
c.execute("SELECT COUNT(DISTINCT platform) FROM profiles WHERE type='core'")
print('Distinct core platforms:', c.fetchone()[0])
c.execute("SELECT COUNT(DISTINCT platform) FROM profiles WHERE type='bio'")
print('Distinct bio platforms:', c.fetchone()[0])

# Check platforms that have BOTH core and bio
c.execute("""
    SELECT COUNT(*) FROM (
        SELECT platform FROM profiles WHERE type='core'
        INTERSECT
        SELECT platform FROM profiles WHERE type='bio'
    )
""")
print('Platforms with BOTH core and bio:', c.fetchone()[0])

conn.close()
