import urllib.request
import json

url = "http://localhost:8000/api/active_floats?startDate=2026-05-14&endDate=2026-05-14"
resp = urllib.request.urlopen(url)
d = json.loads(resp.read())

print(f"Total floats: {d['count']}")
print(f"Core: {d['core_count']}")
print(f"BGC: {d['bgc_count']}")
print(f"INCOIS Total (from metadata): {d['incois_total']}")
print(f"INCOIS Visible (in date range): {d['incois_visible']}")
print(f"Ocean counts: {d['ocean_counts']}")
print(f"Institution counts: {d['inst_counts']}")
