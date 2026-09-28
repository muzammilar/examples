// Collection `events` (3 shards, replicationFactor 2) with 100 documents; prints the DB-server
// that leads the first shard (hostname and short name): the one `make failover` stops
const {assert, health, shards, waitFor} = require('/js/lib.js');
if (!db._databases().includes('demo')) { db._createDatabase('demo'); }
db._useDatabase('demo');
if (db._collection('events')) { db._drop('events'); }
const events = db._create('events', {numberOfShards: 3, replicationFactor: 2});
events.insert(Array.from({length: 100}, (_, i) => ({_key: 'before-' + i, phase: 'before'})));
assert(events.count() === 100, '100 documents');
waitFor('events shards in sync', 30, () => Object.values(shards('demo', 'events')).every(s => s.inSync));
const dist = shards('demo', 'events');
Object.keys(dist).forEach(k => print('  ' + k + ': leader ' + dist[k].leader + ', follower ' + dist[k].followers.join(',')));
const victim = dist[Object.keys(dist)[0]].leader;
// last line of output, read by `make failover`; the short name identifies the server in
// /_admin/cluster/health once it is down (its endpoint is then empty)
print(victim + ' ' + health().find(s => s.host === victim).shortName);
