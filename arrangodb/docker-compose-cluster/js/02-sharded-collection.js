// A collection split into 3 shards, each kept on 2 DB-servers (leader + follower)
const {assert, shards, waitFor} = require('/js/lib.js');
if (!db._databases().includes('demo')) { db._createDatabase('demo'); }
db._useDatabase('demo');
if (db._collection('orders')) { db._drop('orders'); }
const orders = db._create('orders', {numberOfShards: 3, replicationFactor: 2});

const docs = [];
for (let i = 1; i <= 1000; i++) {
  docs.push({_key: 'o' + i, customer: 'c' + (i % 10), amount: i % 100, status: i % 3 ? 'paid' : 'open'});
}
orders.insert(docs);
assert(orders.count() === 1000, '1000 documents, got ' + orders.count());
print('inserted ' + orders.count() + ' orders; properties: ' +
  JSON.stringify({numberOfShards: orders.properties().numberOfShards, replicationFactor: orders.properties().replicationFactor}));

// the followers join asynchronously after creation; wait (bounded) until Current = Plan
waitFor('orders shards in sync', 30, () => Object.values(shards('demo', 'orders')).every(s => s.inSync));
const dist = shards('demo', 'orders');
const perShard = orders.count(true); // documents per shard
Object.keys(dist).forEach(k => print('  ' + k + ': leader ' + dist[k].leader + ', follower ' + dist[k].followers.join(',') +
  ', ' + perShard[k] + ' docs, in sync ' + dist[k].inSync));
Object.values(dist).forEach(s => assert(s.followers.length === 1 && s.followers[0] !== s.leader, 'leader + 1 other follower per shard'));
const onServers = new Set([].concat(...Object.values(dist).map(s => [s.leader].concat(s.followers))));
assert(Object.keys(dist).length === 3, '3 shards');
assert(onServers.size === 3, 'shards spread over all 3 DB-servers, got ' + [...onServers]);
print('ok: 3 shards x 2 replicas over ' + [...onServers].sort().join(', '));
