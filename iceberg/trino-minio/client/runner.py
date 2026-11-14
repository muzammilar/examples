"""Trino's HTTP protocol, using only Python's standard library."""
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path('/results')
RUN = ROOT / os.environ.get('RUN_ID', 'manual')
URL = os.environ.get('TRINO_URL', 'http://coordinator:8080')
HEADERS = {'X-Trino-User': 'example', 'X-Trino-Catalog': 'iceberg',
           'X-Trino-Schema': 'example'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def request(url, data=None):
    req = urllib.request.Request(url, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


def active_worker(info):
    addresses = {socket.gethostbyname(name): name
                 for name in ('worker1', 'worker2', 'worker3')}

    for stage in info.get('stages', {}).get('stages', []):
        for task in stage.get('tasks', []):
            status = task.get('taskStatus', {})
            host = urllib.parse.urlparse(status.get('self', '')).hostname
            if status.get('state') == 'RUNNING' and host in addresses:
                return addresses[host]
    return None


def query(sql, label, fault=False):
    start = time.monotonic()
    page = request(URL + '/v1/statement', sql.encode())
    query_id = page['id']
    rows = []
    armed = False
    while True:
        if 'error' in page:
            save(RUN / (label + '-error.json'), page)
            raise RuntimeError(page['error'])
        rows.extend(page.get('data', []))
        if fault and not armed:
            # The statement queue returns an ID before the query manager registers it.
            try:
                info = request(URL + '/v1/query/' + query_id)
            except urllib.error.HTTPError as error:
                if error.code != 410 or page.get('stats', {}).get('state') != 'QUEUED':
                    raise
                info = {}
            worker = active_worker(info)
            if worker:
                save(RUN / 'ready.json', {'query_id': query_id, 'worker': worker})
                armed = True
        if 'nextUri' not in page:
            break
        if time.monotonic() - start > 600:
            raise TimeoutError(query_id)
        page = request(page['nextUri'])
    info = request(URL + '/v1/query/' + query_id)
    result = {'query_id': query_id, 'elapsed_seconds': time.monotonic() - start,
              'rows': rows, 'stats': page.get('stats'), 'query_stats': info.get('queryStats'),
              'stage_stats': [stage.get('stageStats') for stage in
                              info.get('stages', {}).get('stages', [])]}
    save(RUN / (label + '.json'), result)
    if fault and not armed:
        raise RuntimeError('Query finished before a running worker task could be selected')
    print(json.dumps({'query_id': query_id, 'label': label,
                      'elapsed_seconds': result['elapsed_seconds'], 'rows': rows}))
    return result


def ids(count):
    return (f'SELECT CAST(a * 1000 + b AS bigint) AS id '
            f'FROM UNNEST(sequence(0, {(count - 1) // 1000})) x(a) '
            f'CROSS JOIN UNNEST(sequence(0, 999)) y(b) WHERE a * 1000 + b < {count}')


def seed():
    count = int(os.environ.get('COUNT', '1000000'))
    customers = int(os.environ.get('CUSTOMERS', '10000'))
    if not 1 <= count <= 10000000 or not 1 <= customers <= 1000000:
        raise ValueError('COUNT must be 1..10000000; CUSTOMERS must be 1..1000000')
    query('CREATE SCHEMA IF NOT EXISTS iceberg.example', 'schema')
    query('DROP TABLE IF EXISTS iceberg.example.orders', 'drop-orders')
    query('DROP TABLE IF EXISTS iceberg.example.customers', 'drop-customers')
    query(f'CREATE TABLE customers WITH (format=\'PARQUET\') AS '
          f'SELECT id AS customer_id FROM ({ids(customers)})', 'customers')
    committed = query("CREATE TABLE orders WITH (format='PARQUET', "
                      "partitioning=ARRAY['bucket(customer_id, 16)']) AS "
                      f'SELECT id AS op_id, (id / 100) % {customers} AS customer_id, '
                      f'id % 10000 + 1 AS amount FROM ({ids(count)})', 'orders')
    snapshots = query('SELECT snapshot_id FROM "orders$snapshots"', 'snapshots')['rows']
    witness = {'count': count, 'customers': customers, 'snapshot_ids': snapshots,
               'committed_query_id': committed['query_id'], 'dataset': RUN.name}
    save(RUN / 'acknowledged.json', witness)
    save(ROOT / 'current.json', witness)
    check()


def witness():
    return json.loads((ROOT / 'current.json').read_text())


def check():
    acknowledged = witness()
    count = acknowledged['count']
    customers = acknowledged['customers']
    rows = query('SELECT count(*), count(DISTINCT op_id), min(op_id), max(op_id), '
                 'sum(amount), count_if(o.customer_id <> (op_id / 100) % '
                 f'{customers} OR amount <> op_id % 10000 + 1) '
                 'FROM orders o JOIN customers c ON c.customer_id=o.customer_id', 'check')['rows']
    whole, tail = divmod(count, 10000)
    expected = [[count, count, 0, count - 1, whole * 50005000 + tail * (tail + 1) // 2, 0]]
    if rows != expected:
        raise AssertionError({'observed': rows, 'expected': expected})
    snapshots = query('SELECT snapshot_id FROM "orders$snapshots"', 'snapshots')['rows']
    if not all(item in snapshots for item in acknowledged['snapshot_ids']):
        raise AssertionError('Acknowledged snapshot disappeared')
    save(RUN / 'reconciliation.json', {'acknowledged': count, 'observed': rows[0][0],
                                     'missing_acknowledged': 0, 'duplicate_ids': 0,
                                     'amount_sum': rows[0][4], 'snapshots': snapshots})


def heavy(fault=False):
    amplify = int(os.environ.get('AMPLIFY', '256'))
    if not 1 <= amplify <= 256:
        raise ValueError('AMPLIFY must be 1..256')
    result = query('SELECT count(*), sum(amount), '
                   "to_hex(checksum(sha256(to_utf8(cast(op_id AS varchar) || '-' || cast(d AS varchar))))) "
                   f'FROM orders CROSS JOIN UNNEST(sequence(1, {amplify})) t(d)', 'heavy', fault)
    reference = ROOT / witness()['dataset'] / f'checksum-{amplify}.json'
    if fault:
        expected = json.loads(reference.read_text())
        if result['rows'] != expected:
            raise AssertionError('Heavy query result changed after fault')
    else:
        save(reference, result['rows'])


if __name__ == '__main__':
    RUN.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    if mode == 'seed':
        seed()
    elif mode == 'history':
        from history import history
        history(query, save, RUN, witness(), ids)
    elif mode == 'check':
        check()
    elif mode == 'heavy':
        heavy('--fault' in sys.argv)
    elif mode == 'drop':
        query('DROP TABLE IF EXISTS history_unpartitioned', 'drop-history-unpartitioned')
        query('DROP TABLE IF EXISTS order_history', 'drop-history')
        query('DROP TABLE IF EXISTS orders', 'drop-orders')
        query('DROP TABLE IF EXISTS customers', 'drop-customers')
        query('DROP SCHEMA IF EXISTS iceberg.example', 'drop-schema')
    else:
        raise ValueError(mode)
