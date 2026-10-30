"""Model-free local scheduler. Disabled until explicit activation approval.

No task registration, startup change, or implicit download occurs in this script.
"""
import datetime as dt,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
KST=dt.timezone(dt.timedelta(hours=9))

def latest_due(now,times):
    candidates=[]
    for offset in (0,1):
        day=now.date()-dt.timedelta(days=offset)
        for text in times:
            h,m=map(int,text.split(':'));slot=dt.datetime.combine(day,dt.time(h,m),KST)
            if slot<=now:candidates.append(slot)
    return max(candidates)

def main():
    print('예약 실행은 폐기되었습니다. 오늘 쓸 주제 찾기.cmd를 직접 실행하세요.')
    return 2
    # Archived implementation below is deliberately unreachable.
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'))
    if not cfg['schedule']['enabled']:
        print('예약 실행은 승인 대기 상태입니다. 실행하지 않았습니다.');return 2
    # Independent single-instance lock. run.py also prevents overlapping analyses.
    import msvcrt
    lock=open(ROOT/'data'/'scheduler.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:return 2
    state_path=ROOT/'data'/'scheduler-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    try:
        while True:
            cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'))
            if not cfg['schedule']['enabled']:return 0
            now=dt.datetime.now(KST);slot=latest_due(now,cfg['schedule']['times']).isoformat()
            if slot!=state.get('last_attempted_slot'):
                # One newest catch-up only. Failed runs do not retry indefinitely.
                state={'last_attempted_slot':slot,'started':now.isoformat()}
                state_path.write_text(json.dumps(state),encoding='utf8')
                p=subprocess.run([sys.executable,str(ROOT/'run.py')],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                state.update(exit_code=p.returncode,ended=dt.datetime.now(KST).isoformat())
                state_path.write_text(json.dumps(state),encoding='utf8')
                (ROOT/'data'/'scheduler-last.log').write_bytes(p.stdout)
            time.sleep(30)
    finally:lock.close()
if __name__=='__main__':raise SystemExit(main())
