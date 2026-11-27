"""Curate local-mode correctness/recovery diagnostics from executed evidence."""
import datetime
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def main():
    results = Path('results')
    baseline = max(results.glob('write-*/receipt.json'),key=lambda p:p.stat().st_mtime).parent
    fault = max(results.glob('reader-crash-*/receipt.json'),key=lambda p:p.stat().st_mtime)
    recovered = max(results.glob('verify-*/receipt.json'),key=lambda p:p.stat().st_mtime).parent
    assert (recovered/'receipt.json').stat().st_mtime > fault.stat().st_mtime, 'Recovery oracle must follow crash'
    before, after = read(baseline/'parquet.json'), read(recovered/'parquet.json')
    assert before['file_manifest'] == after['file_manifest'], 'Published file body or filename changed'
    logs = (baseline/'write.log').read_text()
    counters = [json.loads(line.removeprefix('WORKLOAD_RESOURCE_RESULT ')) for line in logs.splitlines() if line.startswith('WORKLOAD_RESOURCE_RESULT ')]
    assert len(counters)==1, 'Expected complete native writer counters'
    actors, environment = {}, None
    for history in results.glob('resources-*.jsonl'):
        for line in history.read_text().splitlines():
            row = json.loads(line)
            if row['type']=='environment':
                environment = row['environment']
                continue
            for actor in row['actors']:
                if 'counters' not in actor:
                    continue
                record = actors.setdefault(actor['id'], {'names':actor['names'],'samples':[]})
                if 'configuration' in actor:record['configuration']=actor['configuration']
                record['samples'].append((row['elapsed_s'],actor['counters']))
    summary = []
    for identifier, actor in actors.items():
        points = actor.pop('samples')
        cpu = [(t,x.get('cpu_stats',{}).get('cpu_usage',{}).get('total_usage')) for t,x in points]
        cpu = [(t,v) for t,v in cpu if v is not None]
        deltas = [(b[1]-a[1])/(b[0]-a[0])/1e7 for a,b in zip(cpu,cpu[1:]) if b[0]-a[0]>=0.05 and b[1]>=a[1]]
        memory = [x.get('memory_stats',{}).get('usage') for _,x in points]
        memory = [m for m in memory if m is not None]
        io = []
        for _,x in points:
            values=x.get('blkio_stats',{}).get('io_service_bytes_recursive')
            io.append({op:sum(v['value'] for v in values if v['op'].lower()==op) for op in ('read','write')} if values else None)
        summary.append({**actor,'id':identifier,'samples':len(points),
                        'sampled_cpu_mean_pct_one_core':sum(deltas)/len(deltas) if deltas else None,
                        'sampled_cpu_peak_pct_one_core':max(deltas,default=None),
                        'sampled_memory_max_bytes':max(memory,default=None),
                        'observed_io_byte_deltas_lower_bound':{k:io[-1][k]-io[0][k] for k in ('read','write')} if io and io[0] is not None and io[-1] is not None else None})
    report = {'tested_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'qualification':'Initial finite correctness and reader-process recovery diagnostics; no competitive performance claim',
              'versions':{'spark':'4.3.0','arrow':'26.0.1'},'environment':environment,
              'baseline':read(baseline/'receipt.json'),'reader_crash':read(fault),
              'recovered':read(recovered/'receipt.json'),'published_file_manifest_unchanged':True,
              'arrow_oracle':after,'writer_pre_exit_cgroup_counters':counters[0],
              'sampled_actors':summary,
              'sources':read(Path('.build/source-inventory.json')),
              'limitations':['Single Docker volume/host, no replicated durability or independent backup.',
                  'Local scheduling/execution shares one JVM; Python is an additional driver process.',
                  'Reader SIGKILL does not qualify interrupted writer publication or native recovery of a killed driver.',
                  'Startup/exit intervals can be missed by samples; native pre-exit counters cover the normal writer and Arrow reader, not the killed actor final interval.',
                  'Byte I/O uses Docker/cgroup counters; device service latency, full network accounting and host-independent cold reads unavailable.',
                  'One trial per path, elapsed application times include startup and verification; no sustained capacity or comparative winner.',
                  'No physical power-loss or fsync durability proof.']}
    Path('reports/INITIAL_RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
