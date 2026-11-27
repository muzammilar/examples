"""Run finite local Spark work and fresh readbacks into immutable result paths."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

COMPOSE = ['docker','compose','--project-name','spark-local-orders']
ARGS = ['--master','local[1]','--driver-memory','1g','--conf','spark.sql.shuffle.partitions=8',
        '/opt/spark/orders-workload/orders.py']


def main():
    mode = sys.argv[1]
    if mode not in ('write','verify'):
        raise ValueError(mode)
    directory = Path('results') / (mode + '-' + str(time.time_ns()))
    directory.mkdir(parents=True)
    expected_hash = hashlib.sha256(Path('orders-workload/orders.py').read_bytes()).hexdigest()
    phases = {}
    for phase in (['write','verify'] if mode == 'write' else ['verify']):
        started = time.monotonic()
        with (directory/(phase+'.log')).open('x') as log:
            subprocess.run([*COMPOSE,'run','--rm','orders',*ARGS,phase],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
        phases[phase] = {'elapsed_s':time.monotonic()-started}
        body = (directory/(phase+'.log')).read_text()
        if 'WORKLOAD_SOURCE_SHA256 '+expected_hash not in body or 'VERIFIED_RESULT ' not in body:
            raise ValueError('Missing native verification or mismatched deployed source')
    started = time.monotonic()
    with (directory/'parquet.log').open('x') as log:
        subprocess.run([*COMPOSE,'run','--rm','parquet-verifier'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
    lines = (directory/'parquet.log').read_text().splitlines()
    values = [json.loads(line) for line in lines if line.startswith('{"verifier_source_sha256":')]
    if len(values) != 1:
        raise ValueError('Expected one independent oracle result')
    result = values[0]
    if result['verifier_source_sha256'] != hashlib.sha256(Path('parquet-verifier/verify.py').read_bytes()).hexdigest():
        raise ValueError('Mismatched deployed Arrow oracle source')
    phases['arrow'] = {'elapsed_s':time.monotonic()-started}
    (directory/'parquet.json').write_text(json.dumps(result,indent=2)+'\n')
    receipt = {'mode':mode,'phases':phases,'orders_source_sha256':expected_hash,
               'oracle_source_sha256':result['verifier_source_sha256'],'orders':result['orders'],
               'amount_cents':result['amount_cents'],'missing':result['missing'],'wrong':result['wrong']}
    with (directory/'native-events.tar').open('xb') as archive, (directory/'archive.log').open('x') as log:
        subprocess.run([*COMPOSE,'run','--rm','--entrypoint','tar','orders','-C','/data','-cf','-','events'],stdout=archive,stderr=log,check=True,timeout=60)
    (directory/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'qualification':'passed','result_directory':str(directory),**receipt}))


if __name__ == '__main__':
    main()
