"""Kill one local read application; preserve published data for fresh verification."""
import json
from pathlib import Path
import subprocess
import time

COMPOSE = ['docker', 'compose', '--project-name', 'spark-local-orders']
NAME = 'spark-local-read-crash'


def main():
    directory = Path('results') / ('reader-crash-' + str(time.time_ns()))
    directory.mkdir(parents=True)
    if subprocess.run(['docker', 'container', 'inspect', NAME], capture_output=True).returncode == 0:
        raise RuntimeError('Previous reader container exists; preserve/inspect it first')
    command = [*COMPOSE, 'run', '--name', NAME, '--no-deps', 'orders', '--master', 'local[1]',
               '--driver-memory', '1g', '--conf', 'spark.sql.shuffle.partitions=8',
               '/opt/spark/orders-workload/orders.py', 'verify']
    started = time.monotonic()
    owned = False
    with (directory / 'reader.log').open('x') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            with (directory / 'probes.jsonl').open('x') as history:
                while time.monotonic() - started < 90 and child.poll() is None:
                    code = ('import json,urllib.request; base="http://127.0.0.1:4040/api/v1"; '
                            'apps=json.load(urllib.request.urlopen(base+"/applications",timeout=2)); '
                            'print(json.dumps(json.load(urllib.request.urlopen(base+"/applications/"+apps[0]["id"]+"/executors",timeout=2))))')
                    probe = subprocess.run(['docker','exec',NAME,'python3','-c',code], capture_output=True, text=True, timeout=8)
                    history.write(json.dumps({'elapsed_s':time.monotonic()-started,'code':probe.returncode,
                                              'stdout':probe.stdout,'stderr':probe.stderr})+'\n')
                    history.flush()
                    if probe.returncode == 0:
                        observed = json.loads(probe.stdout)
                        if any(e.get('activeTasks',0)>0 for e in observed):
                            info = json.loads(subprocess.check_output(['docker','inspect',NAME],text=True))[0]
                            if info['Config']['Labels'].get('com.docker.compose.project') != 'spark-local-orders':
                                raise RuntimeError('Unexpected container ownership')
                            owned = True
                            subprocess.run(['docker','kill','--signal','KILL',NAME],check=True)
                            child.wait(timeout=20)
                            final = json.loads(subprocess.check_output(['docker','inspect',NAME],text=True))[0]
                            if final['State']['Running'] or final['State']['ExitCode'] != 137:
                                raise RuntimeError('Native SIGKILL exit 137 not observed')
                            (directory/'container.json').write_text(json.dumps(final,indent=2)+'\n')
                            receipt = {'observed_active_tasks':observed,'exit_code':137,
                                       'elapsed_s':time.monotonic()-started,
                                       'mode':'Read-only process crash; fresh verification is required separately',
                                       'container_id':info['Id']}
                            (directory/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
                            print(json.dumps({'reader_crash':'observed','evidence':str(directory)}))
                            return
                    time.sleep(0.2)
            raise RuntimeError('No active native reader task observed; this is not a passing crash test')
        finally:
            current = subprocess.run(['docker','inspect',NAME],capture_output=True,text=True)
            if current.returncode == 0:
                info = json.loads(current.stdout)[0]
                owned = info['Config']['Labels'].get('com.docker.compose.project') == 'spark-local-orders'
                if owned and info['State']['Running']:
                    subprocess.run(['docker','stop','--time','10',NAME],check=True)
            if child.poll() is None:
                child.wait(timeout=20)
            if owned:
                subprocess.run(['docker','rm',NAME],check=True)


if __name__ == '__main__':
    main()
