"""Order corrections, Iceberg snapshots and a tenant dashboard over shared Parquet."""
import math
import os
import statistics


def history(query, save, directory, acknowledged, ids):
    count = acknowledged['count']
    customers = acknowledged['customers']
    appended = max(1, count // 10)
    target = min(42, customers - 1)
    repetitions = int(os.environ.get('HISTORY_QUERIES', '20'))
    if not 2 <= repetitions <= 100:
        raise ValueError('HISTORY_QUERIES must be 2..100')
    operations = {}

    def execute(sql, label):
        result = query(sql, 'history-' + label)
        operations[label] = result['elapsed_seconds']
        return result

    def snapshot(label):
        return execute('SELECT snapshot_id FROM "order_history$snapshots" '
                       'ORDER BY committed_at DESC LIMIT 1', label)['rows'][0][0]

    def expected(size, corrected=False):
        whole, tail = divmod(size, 10000)
        total = whole * 50005000 + tail * (tail + 1) // 2
        selected = [i for i in range(size) if (i // 100) % customers == target]
        tenant_sum = sum(i % 10000 + 1 for i in selected)
        if corrected:
            total += 10 * len(selected)
            tenant_sum += 10 * len(selected)
        return total, len(selected), tenant_sum

    def check(sql, wanted, label):
        observed = execute(sql, label)['rows']
        if observed != [wanted]:
            raise AssertionError({'query': label, 'observed': observed, 'expected': wanted})

    execute('DROP TABLE IF EXISTS history_unpartitioned', 'drop-unpartitioned')
    execute('DROP TABLE IF EXISTS order_history', 'drop')
    execute("CREATE TABLE order_history WITH (format='PARQUET', "
            "partitioning=ARRAY['bucket(customer_id, 16)']) AS SELECT * FROM orders", 'create')
    original = snapshot('snapshot-original')
    execute('ALTER TABLE order_history ADD COLUMN channel varchar', 'add-column')
    execute('INSERT INTO order_history SELECT id, (id / 100) % '
            f"{customers}, id % 10000 + 1, 'web' FROM "
            f'(SELECT id + {count} AS id FROM ({ids(appended)}))', 'append')
    before_correction = snapshot('snapshot-appended')
    total, tenant_rows, tenant_sum = expected(count + appended, True)
    execute(f'UPDATE order_history SET amount=amount+10 WHERE customer_id={target}', 'correct')
    corrected = snapshot('snapshot-corrected')
    check('SELECT count(*),count(DISTINCT op_id),sum(amount),count_if(channel IS NULL),'
          "count_if(channel='web') FROM order_history",
          [count + appended, count + appended, total, count, appended], 'latest-check')
    check(f'SELECT count(*),sum(amount) FROM order_history FOR VERSION AS OF {original}',
          [count, expected(count)[0]], 'original-check')
    check(f'SELECT count(*),sum(amount) FROM order_history FOR VERSION AS OF {before_correction}',
          [count + appended, expected(count + appended)[0]], 'before-correction-check')
    execute("CREATE TABLE history_unpartitioned WITH (format='PARQUET') AS "
            'SELECT * FROM order_history', 'create-unpartitioned')
    phases = {}
    for table in ('order_history', 'history_unpartitioned'):
        samples, input_positions, input_bytes = [], [], []
        for index in range(repetitions):
            result = query(f'SELECT count(*),coalesce(sum(amount),0) FROM {table} '
                           f'WHERE customer_id={target}', f'history-{table}-{index}')
            if result['rows'] != [[tenant_rows, tenant_sum]]:
                raise AssertionError('Tenant dashboard changed between layouts')
            samples.append(result['elapsed_seconds'] * 1000)
            stats = result['query_stats']
            input_positions.append(stats.get('physicalInputPositions'))
            input_bytes.append(stats.get('physicalInputDataSize'))
        ordered = sorted(samples)
        phases[table] = {'queries': repetitions, 'p50_ms': statistics.median(samples),
                         'p99_ms': ordered[math.ceil(.99 * repetitions) - 1],
                         'max_ms': max(samples), 'physical_input_positions': input_positions,
                         'physical_input_bytes': input_bytes}
    result = {'original_rows': count, 'appended_rows': appended, 'current_rows': count + appended,
              'current_amount_sum': total, 'tenant_id': target, 'tenant_rows': tenant_rows,
              'tenant_amount_sum': tenant_sum, 'old_rows_with_null_new_column': count,
              'snapshots': {'original': original, 'appended': before_correction,
                            'corrected': corrected}, 'operations_seconds': operations,
              'dashboard_phases': phases}
    save(directory / 'order-history.json', result)
    print(result)
