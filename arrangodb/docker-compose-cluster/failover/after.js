// ARGUMENTS[0] = the restarted DB-server: GOOD again, and every shard's followers are in sync
const {assert, health, printHealth, shards, waitFor} = require('/js/lib.js');
const back = ARGUMENTS[0];
db._useDatabase('demo');
let t = waitFor(back + ' GOOD again', 120, () => health().every(s => s.status === 'GOOD'));
print('all members GOOD after ' + t + ' s');
printHealth();
const inSync = () => db._collections().filter(c => !c.name().startsWith('_'))
  .every(c => Object.values(shards('demo', c.name())).every(s => s.inSync && s.followers.length === 1));
t = waitFor('every shard in demo in sync (Current = Plan, 2 replicas)', 120, inSync);
const dist = shards('demo', 'events');
print('events shards in sync after ' + t + ' more s:');
Object.keys(dist).forEach(k => print('  ' + k + ': leader ' + dist[k].leader + ', follower ' + dist[k].followers.join(',') + ', in sync ' + dist[k].inSync));
assert(db.events.count() === 200, '200 documents');
print('ok: ' + back + ' is back, all shards have 2 in-sync replicas');
