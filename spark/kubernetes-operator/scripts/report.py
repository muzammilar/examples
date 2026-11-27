"""Curate executed recovery evidence; timings are diagnostic, not competitive."""
import datetime
import json
from pathlib import Path
import re

import kube


def read(path):
    return json.loads(path.read_text())


def main():
    root = kube.ROOT / 'results'
    fault_dir = max(root.glob('executor-recovery-*/orders-write-result.json'), key=lambda p:p.stat().st_mtime).parent
    replacement = max(root.glob('storage-replace-*/replacement-result.json'), key=lambda p:p.stat().st_mtime)
    proof = read(fault_dir / 'native-fault-proof.json')
    samples = {}
    for history in root.glob('*/*-history.jsonl'):
        for line in history.read_text().splitlines():
            record = json.loads(line)
            stamp = datetime.datetime.fromisoformat(record['utc']).timestamp()
            for pod in record['pods']:
                name, uid = pod['metadata']['name'], pod['metadata']['uid']
                counter = record['cgroups'].get(name, {})
                if counter.get('code') != 0:
                    continue
                text = counter['stdout']
                cpu = re.search(r'^usage_usec (\d+)', text, re.M)
                memory = re.search(r'^memory.peak\n(\d+)', text, re.M)
                current = re.search(r'^memory.current\n(\d+)', text, re.M)
                io = {key:sum(map(int,re.findall(r'\b'+key+r'=(\d+)',text))) for key in ('rbytes','wbytes','rios','wios')}
                if cpu and memory and current:
                    samples.setdefault(uid, {'pod':name,'worker':pod['spec']['nodeName'],'values':[]})['values'].append(
                        (stamp,int(cpu[1]),int(memory[1]),int(current[1]),io))
    resources = []
    for uid, body in samples.items():
        points = sorted({p[0]:p for p in body.pop('values')}.values())
        cpu_pct = [(b[1]-a[1])/(b[0]-a[0])/10000 for a,b in zip(points,points[1:]) if b[0]>a[0] and b[1]>=a[1]]
        wall = points[-1][0]-points[0][0]
        resources.append({**body,'pod_uid':uid,'samples':len(points),'observed_seconds':wall,
                          'sampled_cpu_mean_pct_one_core':(points[-1][1]-points[0][1])/wall/10000 if wall else None,
                          'sampled_cpu_peak_pct_one_core':max(cpu_pct,default=None),
                          'observed_native_memory_peak_bytes':max(p[2] for p in points),
                          'charged_memory_mean_bytes':sum(p[3] for p in points)//len(points),
                          'cpu_core_seconds_observed_lower_bound':(points[-1][1]-points[0][1])/1e6,
                          'io_counter_observed_deltas':{key:points[-1][4][key]-points[0][4][key] for key in points[0][4]}})
    disks = {}
    for pod in kube.pods():
        role = pod['metadata'].get('labels',{}).get('app')
        if role not in ('hdfs-namenode','hdfs-datanode'):
            continue
        path = '/data/name' if role == 'hdfs-namenode' else '/data/data'
        name = pod['metadata']['name']
        disks[name] = {'logical_du_bytes':int(kube.run('exec',name,'--','du','-sb',path).stdout.split()[0]),
                       'allocated_du_bytes':int(kube.run('exec',name,'--','du','-sk',path).stdout.split()[0])*1024}
    oracle = read(fault_dir/'independent-readback.json')
    replacement_result = read(replacement)
    replacement_result.pop('final_resources', None)
    environment = read(root/'docker-environment.json')
    report = {'environment':{'cpus':environment['NCPU'],'memory_bytes':environment['MemTotal'],
                  'architecture':environment['Architecture'],'kernel':environment['KernelVersion'],
                  'docker_server':environment['ServerVersion'],
                  'kubernetes':read(root/'kubernetes-version.json'),
                  'disk_class':'Docker Desktop VM overlay; underlying device not measured',
                  'co_tenancy':'Only this example lab was running; kind workers share one VM',
                  'effective_role_limits':{p['metadata']['name']:[c.get('resources',{}) for c in p['spec']['containers']] for p in kube.pods()}},
              'tested_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'qualification':'Initial functional/recovery diagnostics only; no performance comparison',
              'versions':{'spark':'4.3.0','operator':'1.1.0','operator_chart':'1.9.0','hadoop':'3.5.0','pyarrow':'26.0.1'},
              'executor_recovery':{**read(fault_dir/'orders-write-result.json'),
                  'native_matching_removals':len(proof['matching_executor_removals']),
                  'native_matching_failed_tasks':len(proof['matching_failed_tasks'])},
              'fresh_oracle':oracle,'empty_disk_replacement':replacement_result,
              'final_three_copy_audit':read(replacement.parent/'replica-verdict-3.json'),
              'effective_hdfs_config':read(root/'effective-hdfs-config.json'),
              'resources':resources,'final_native_storage_directories':disks,
              'observed_setup_failures':['Image-import preflight underestimated compressed-content plus overlay snapshot demand; corrected conservative bound. Removed authorized unused build cache and own host-built images after worker import.',
                  'DataNode EPERM changing PVC-root permissions; corrected mount parent so non-root process owns its native data subdirectory. Retained NameNode and PVC state.'],
              'limitations':['All workers share one Docker VM; one NameNode; no metadata HA/restore qualification.',
                  'Finite bulk publication, not an external per-record ACK journal; no continuous writes during disk replacement.',
                  'Physical replica hashes cover published Parquet files, not all Spark event logs or metadata.',
                  'Sampled CPU/IO omit unobserved start/exit intervals. Native peaks include charged cache. Device service latency, network byte accounting and portable host cache control unmeasured.',
                  'HDFS replicated physical bytes and Parquet column compression are distinct layers. File hashes are not a physical power-loss test.',
                  'Source event-proof controller ran separately on this first trial; integrated target requires a fresh repeat to qualify exact final orchestration.',
                  'Further failure, lifecycle and matched level-3 benchmark coverage remains pending.']}
    (kube.ROOT/'reports/INITIAL_RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
