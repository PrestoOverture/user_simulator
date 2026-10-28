"""A-only watchdog: resume unexpected exits, preserve stops, verify delivery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_correction_decision_ppi as p
from usersim.judge_spark import cache_writer,read_cache,JudgeStopped
OUT=p.OUT


def stop_reason(out):
    cache=out/'spark_judgments.jsonl'
    marker=Path(str(cache)+'.stop.json')
    if marker.exists():return p.a.read(marker)
    try:p.enforce_spark_parse_rate(p.spark_failure_counts(read_cache(cache,allow_pending=True)))
    except JudgeStopped as exc:return dict(reason=exc.reason,message=str(exc))
    except (ValueError,RuntimeError) as exc:return dict(reason='cache_integrity',message=str(exc))
    runtime=Path(str(cache)+'.runtime.json')
    if runtime.exists():
        state=p.a.read(runtime)
        if not state.get('batch_complete') and time.time()-state['last_success_wall_time']>=3600:
            return dict(reason='no_success_60_minutes')
    if (out/'STOPPED.md').exists():
        message=(out/'STOPPED.md').read_text()
        critical=('parse failure','parse failures','parse_error','4900','10000','in-flight reserve',
                  'cannot verify gateway billing','retry budget exhausted','non-transient',
                  'projection exceeds','post-hoc authorization','missing actual','c preregistered','c preregistration','frozen','mismatch',
                  'active gemini cache writer','outside preregistered','unresolved','duplicate','incompatible','api_key','base_url')
        if any(word in message.lower() for word in critical):
            return dict(reason='pipeline_stop',message=message)
    # Recover orphan attempts only after proving no other writer is alive.
    rows=p.a.lines(out/'gemini_sample.jsonl')
    dispatch={(r['id'],r['attempt']) for r in rows if r['event']=='dispatch'}
    responses={(r['id'],r['attempt']) for r in rows if r['event']=='response'}
    if dispatch!=responses:
        try:
            with p.sample_writer(out):p.abandon_sample_dispatches(out)
        except RuntimeError as exc:
            return dict(reason='unresolved_gemini_dispatch',message=str(exc))
    return None


def verify_delivery(out):
    commands=[('report_1',[sys.executable,'scripts/run_correction_decision.py','--phase','report','--report-n','500']),
              ('report_2',[sys.executable,'scripts/run_correction_decision.py','--phase','report','--report-n','500']),
              ('check_500',[sys.executable,'scripts/check_correction_decision.py','--n','500']),
              ('pytest',[sys.executable,'-m','pytest','-q']),
              ('protected_diff',['git','diff','--exit-code','--','docs','usersim/correction_v2.py','scripts/measure_reaction.py','outputs/correction_v2'])]
    results=[];hashes=[]
    for name,command in commands:
        with (out/f'guard_{name}.log').open('w') as log:
            code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT).returncode
        if code==0 and name=='check_500':
            try:
                checked=json.loads((out/f'guard_{name}.log').read_text().splitlines()[-1])
                if checked.get('verified') is not True or checked.get('api_calls')!=0:code=1
            except (ValueError,IndexError):code=1
        results.append(dict(name=name,command=command,exit_code=code))
        if code:break
        if name.startswith('report_'):
            hashes.append({name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('report_500.md','summary_500.json')})
    passed=len(results)==len(commands) and all(r['exit_code']==0 for r in results) and len(hashes)==2 and hashes[0]==hashes[1]
    value=dict(passed=passed,commands=results,rebuild_hashes=hashes,api_calls=0)
    p.a.dump(out/'guard_verification.json',value)
    return value


def worker_alive(pid):
    if not pid:return False
    result=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True)
    return result.returncode==0 and 'run_correction_decision.py' in result.stdout and '--phase ppi-full' in result.stdout


def wait_for_sample(pid):
    """Wait for the foreground sample without inspecting in-flight dispatches."""
    while True:
        result=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True)
        if result.returncode!=0 or 'run_correction_decision.py --phase gemini-sample' not in result.stdout:
            return
        time.sleep(5)


def supervise(concurrency=4):
    out=OUT
    with cache_writer(out/'ppi_supervisor'):
        path=out/'ppi_supervisor_state.json'
        prior=p.a.read(path) if path.exists() else {}
        restarts=prior.get('restarts',0);old=prior.get('worker_pid')
        def state(stage,**extra):
            p.a.dump(path,dict(stage=stage,supervisor_pid=os.getpid(),observed_at=time.time(),restarts=restarts,**extra))
        while True:
            reason=stop_reason(out)
            if reason:
                state('stopped',stop=reason);print(f'A supervisor stopped: {reason}',flush=True);return 3
            if worker_alive(old):
                state('waiting_worker',worker_pid=old);time.sleep(5);continue
            old=None
            # Preserve an unexpected Python exception rather than hiding its evidence.
            if (out/'STOPPED.md').exists():
                (out/'STOPPED.md').rename(out/f'guard_unexpected_{time.time_ns()}.md')
            command=[sys.executable,'-u','scripts/run_correction_decision.py','--phase','ppi-full','--concurrency',str(concurrency)]
            with (out/'ppi_guard_worker.log').open('a') as log:
                child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                state('running',worker_pid=child.pid,command=command)
                code=child.wait()
            reason=stop_reason(out)
            if reason:
                state('stopped',worker_pid=child.pid,worker_exit_code=code,stop=reason);return 3
            if code==0:
                state('verifying',worker_pid=child.pid)
                result=verify_delivery(out)
                state('complete' if result['passed'] else 'checks_failed',verification=result)
                return 0 if result['passed'] else 1
            restarts+=1
            state('restarting',worker_pid=child.pid,worker_exit_code=code)
            print(f'Unexpected A exit {code}; resume successful caches in 5 seconds',flush=True)
            time.sleep(5)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--concurrency',type=int,default=4)
    ap.add_argument('--wait-pid',type=int,help='Wait for an existing foreground Gemini sample before cache resume')
    args=ap.parse_args()
    if not 1<=args.concurrency<=8:ap.error('concurrency must be 1..8')
    if args.wait_pid:
        print(f'Waiting for existing sample PID {args.wait_pid}',flush=True)
        wait_for_sample(args.wait_pid)
    raise SystemExit(supervise(args.concurrency))
