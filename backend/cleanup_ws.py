"""
Cleanup script: Removes orphaned old WebSocket handler code from main.py.
Run this once from the backend directory.
"""
import os

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')

with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the good WS handler end and the real _build_active_floats_response
# The good WS handler ends with "            except:\n                pass\n" at the right indentation
# The real _build_active_floats_response has the docstring

# Strategy: Find the SECOND occurrence of the except/pass pattern (the one from the old duplicate)
# and everything between the first and second _build_active_floats_response

# Find the first WS handler ending (the good one, under 'async with WS_SEMAPHORE:')
marker_good_end = '''        except Exception as e:
            print(f"WS Error: {e}")
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except:
                pass'''

# Find the real _build_active_floats_response
marker_real_func = '''async def _build_active_floats_response(startDate: Optional[str], endDate: Optional[str]):
    """Builds the active floats response object. Used by cache and endpoint.'''

pos_good_end = content.find(marker_good_end)
pos_real_func = content.find(marker_real_func)

if pos_good_end == -1:
    print("ERROR: Could not find good WS handler end marker")
    exit(1)
if pos_real_func == -1:
    print("ERROR: Could not find real _build_active_floats_response marker")
    exit(1)

# We want to keep everything up to and including the good end, then a blank line, then the real function
end_of_good = pos_good_end + len(marker_good_end)

cleaned = content[:end_of_good] + "\n\n" + content[pos_real_func:]

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(cleaned)

old_lines = content.count('\n')
new_lines = cleaned.count('\n')
print(f"Cleaned up main.py: removed {old_lines - new_lines} orphaned lines")
print(f"File: {old_lines + 1} lines -> {new_lines + 1} lines")
