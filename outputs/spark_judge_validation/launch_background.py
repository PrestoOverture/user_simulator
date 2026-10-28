"""Launch the authorized full-phase pipeline via nohup in an independent session."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

out = Path(__file__).resolve().parent
root = out.parents[1]
pid_path = out/'background.pid'
if pid_path.exists():
    old_pid = int(pid_path.read_text().strip())
    old = subprocess.run(['ps','-p',str(old_pid),'-o','args='],capture_output=True,text=True)
    if old.returncode==0:
        raise SystemExit(f'Previous PID {old_pid} is still live; inspect before restarting')
command = ['nohup','/bin/bash',str(root/'scripts/run_spark_judge_full.sh')]
with (out/'background.log').open('ab') as log:
    process = subprocess.Popen(command,cwd=root,start_new_session=True,
        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True)
pid_path.write_text(str(process.pid)+'\n')
metadata = dict(pid=process.pid,pgid=os.getpgid(process.pid),command=command,
                started_at=datetime.now(timezone.utc).isoformat(),log=str(out/'background.log'),
                concurrency=int(os.environ.get('SPARK_JUDGE_CONCURRENCY','8')),stages=['supervised full','offline PPI','automatic offline acceptance'])
(out/'background_launch.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(metadata,ensure_ascii=False))
