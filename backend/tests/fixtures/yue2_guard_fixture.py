"""No ML: exercise abrupt runner termination against a sleeping child."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if importlib.util.find_spec('app.yue2_process_guard'):
    from app.yue2_process_guard import bind_runner_job
    bind_runner_job()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
Path(sys.argv[1]).write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}))
time.sleep(120)
