"""
Run this script to clean up main.py by removing orphaned duplicate code.
Execute: python cleanup_mainpy.py
"""
import os

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')

with open(FILE, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Original file: {len(lines)} lines")

# The good WS handler ends at line 965 (0-indexed: 964)  
# Line 965 is "                pass\r\n"
# The real _build_active_floats_response starts at line 1103 (0-indexed: 1102)
# Line 1103 is "async def _build_active_floats_response..."

# Find the exact boundaries
good_end = None
real_func_start = None

for i, line in enumerate(lines):
    stripped = line.strip()
    # Find the second occurrence of the 4-space-indented except/pass (the good WS handler end)
    # It's the one with 12-space indentation (inside async with WS_SEMAPHORE: try:)
    if '            except:' in line and i > 950 and good_end is None:
        # Check if next line is "                pass"
        if i+1 < len(lines) and lines[i+1].strip() == 'pass':
            good_end = i + 1  # include the pass line (0-indexed)
            print(f"Found good WS handler end at line {good_end + 1}")
    
    # Find the real _build_active_floats_response (the one with docstring)
    if 'async def _build_active_floats_response' in line and '"""Builds' in lines[i+1] if i+1 < len(lines) else False:
        real_func_start = i
        print(f"Found real _build_active_floats_response at line {real_func_start + 1}")
        break

if good_end is None or real_func_start is None:
    print("ERROR: Could not find boundaries!")
    print(f"good_end={good_end}, real_func_start={real_func_start}")
    # Debug: print around line 965
    for i in range(960, min(970, len(lines))):
        print(f"  Line {i+1}: {repr(lines[i][:80])}")
    exit(1)

# Keep lines 0..good_end, add a blank line, then lines from real_func_start onwards
result = lines[:good_end + 1] + ['\n'] + lines[real_func_start:]

with open(FILE, 'w', encoding='utf-8') as f:
    f.writelines(result)

removed = len(lines) - len(result)
print(f"Removed {removed} orphaned lines")
print(f"File now has {len(result)} lines")
