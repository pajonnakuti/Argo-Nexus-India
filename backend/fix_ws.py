import os

FILE = r'd:\Argo-Nexus-India\backend\main.py'
lines = open(FILE, 'r', encoding='utf-8').readlines()
# Remove lines 967-1178 (0-indexed: 966 to 1177 inclusive)
# These are the old duplicate WS handler body
result = lines[:966] + lines[1178:]
open(FILE, 'w', encoding='utf-8').writelines(result)
print(f"Removed {1178-966} lines. File now has {len(result)} lines (was {len(lines)})")
