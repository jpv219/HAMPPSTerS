import os

try:
    os.system('rm -r __pycache__')
except:
    pass
try:
    os.system('rm output*')
except:
    pass
os.system('rm *csv')
