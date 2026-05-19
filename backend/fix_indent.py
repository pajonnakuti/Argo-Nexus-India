import os

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')

with open(FILE, 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_func = False
start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if line.startswith('async def _run_export_job('):
        in_func = True
    elif in_func and line.startswith('async def '):
        in_func = False
        
    if in_func:
        if "if not filtered:" in line and i > 1300 and start_idx == -1:
            start_idx = i
        if "except Exception as e:" in line and i > 1400:
            end_idx = i + 2 # include the two error lines

if start_idx != -1 and end_idx != -1:
    for i in range(start_idx, end_idx - 2): # until before except
        if lines[i].strip() != '':
            lines[i] = '    ' + lines[i]
            
    # the except line
    if lines[end_idx - 2].strip() != '':
        lines[end_idx - 2] = '    ' + lines[end_idx - 2]
        
    # the two error lines
    for i in range(end_idx - 1, end_idx + 1):
        if lines[i].strip() != '':
            lines[i] = '    ' + lines[i]

with open(FILE, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed indentation for _run_export_job!")
