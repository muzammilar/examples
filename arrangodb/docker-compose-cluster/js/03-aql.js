// AQL through the coordinator: it plans the query, sends parts to the shards' leaders and merges
const {assert} = require('/js/lib.js');
db._useDatabase('demo');
const rows = db._query(`
  FOR o IN orders
    FILTER o.status == 'paid'
    COLLECT customer = o.customer AGGREGATE n = COUNT(1), total = SUM(o.amount)
    SORT customer
    RETURN {customer, n, total}`).toArray();
rows.forEach(r => print('  ' + JSON.stringify(r)));
assert(rows.length === 10, '10 customers');
assert(rows.reduce((a, r) => a + r.n, 0) === 667, '667 paid orders');
const plan = db._createStatement(`FOR o IN orders FILTER o.status == 'paid' RETURN o`).explain().plan;
const remote = plan.nodes.filter(n => n.type === 'RemoteNode' || n.type === 'GatherNode').map(n => n.type);
print('  execution plan has ' + JSON.stringify(remote) + ' (coordinator <-> DB-servers)');
assert(remote.includes('GatherNode'), 'a GatherNode in the plan');
print('ok: AQL aggregation over 3 shards');
