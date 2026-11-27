"""Match the injected executor to its native Spark removal and failed task history."""
import json
from pathlib import Path
import re
import sys

import kube


def verify(directory):
    result = json.loads((directory / 'orders-write-result.json').read_text())
    fault = result['fault']
    if not fault:
        raise ValueError('No recorded active-task executor fault')
    # The driver's native event-log path identifies this writer independently
    # of later verification applications, whose executor IDs can overlap.
    logs = (directory / 'orders-write-driver.log').read_text()
    paths = re.findall(r'Logging events to hdfs://namenode:9000/lab/events/(orders-write-\d+)\.inprogress', logs)
    if len(set(paths)) != 1:
        raise ValueError('Expected one native writer event-log path')
    raw = kube.run('exec', 'hdfs-tools', '--', 'hdfs', 'dfs', '-cat', '/lab/events/' + paths[0]).stdout
    (directory / 'native-events.jsonl').write_text(raw)
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    # Only the completed writer file is selected; readback histories stay separate.
    starts = [r for r in records if r.get('Event') == 'SparkListenerApplicationStart']
    if len(starts) != 1 or starts[0].get('App ID') != paths[0]:
        raise ValueError('Expected only the writer history; refusing mixed application evidence')
    removed = [r for r in records if r.get('Event') == 'SparkListenerExecutorRemoved'
               and str(r.get('Executor ID')) == fault['executor_id']]
    failed = [r for r in records if r.get('Event') == 'SparkListenerTaskEnd'
              and str(r.get('Task Info', {}).get('Executor ID')) == fault['executor_id']
              and r.get('Task End Reason', {}).get('Reason') != 'Success']
    if not removed or not failed:
        raise ValueError('No native removal and failed task on the injected executor')
    proof = {'application_id': starts[0]['App ID'], 'fault': fault,
             'matching_executor_removals': removed, 'matching_failed_tasks': failed}
    (directory / 'native-fault-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
