// ARGUMENTS = the stopped DB-server's hostname and short name. The agency's supervision marks it FAILED and promotes the
// in-sync follower of every shard it led; the coordinator then routes reads and writes there.
const {assert, health, printHealth, shards, waitFor} = require('/js/lib.js');
const [down, downShort] = ARGUMENTS;
db._useDatabase('demo');
const status = () => health().find(s => s.shortName === downShort).status;

let t = waitFor(down + ' FAILED in /_admin/cluster/health', 120, () => status() === 'FAILED');
print(down + ' is FAILED after ' + t + ' s');
printHealth();

t = waitFor('no shard of events led by ' + down, 60, () => Object.values(shards('demo', 'events')).every(s => s.leader !== down && s.leader !== downShort));
const dist = shards('demo', 'events');
print('leaders after ' + t + ' more s:');
Object.keys(dist).forEach(k => print('  ' + k + ': leader ' + dist[k].leader + ', followers ' + JSON.stringify(dist[k].followers)));

const events = db.events;
events.insert(Array.from({length: 100}, (_, i) => ({_key: 'during-' + i, phase: 'during'})));
const byPhase = db._query('FOR e IN events COLLECT phase = e.phase WITH COUNT INTO n SORT phase RETURN {phase, n}').toArray();
print('writes + reads via the coordinator: ' + JSON.stringify(byPhase));
assert(events.count() === 200, '200 documents, got ' + events.count());
assert(JSON.stringify(byPhase) === '[{"phase":"before","n":100},{"phase":"during","n":100}]', '100 before + 100 during');
assert(events.document('before-0').phase === 'before', 'a document written before the failure is readable');
print('ok: reads and writes work with ' + down + ' down');
