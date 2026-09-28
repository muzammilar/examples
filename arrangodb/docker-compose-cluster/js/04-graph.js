// A general (non-Smart) named graph on the cluster: sharded vertex and edge collections, traversal
const {assert} = require('/js/lib.js');
const graphs = require('@arangodb/general-graph');
db._useDatabase('demo');
if (graphs._exists('social')) { graphs._drop('social', true); }
graphs._create('social', [graphs._relation('knows', ['people'], ['people'])], [],
  {numberOfShards: 3, replicationFactor: 2});

const people = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank'];
db.people.insert(people.map(p => ({_key: p, name: p})));
const knows = [['alice', 'bob'], ['alice', 'carol'], ['bob', 'dave'], ['carol', 'dave'], ['dave', 'eve'], ['eve', 'frank']];
db.knows.insert(knows.map(([a, b]) => ({_from: 'people/' + a, _to: 'people/' + b})));
print('people: ' + db.people.count() + ' in ' + db.people.properties().numberOfShards + ' shards, knows: ' +
  db.knows.count() + ' in ' + db.knows.properties().numberOfShards + ' shards');

// friends and friends-of-friends of alice (1..2 hops), unique vertices
const fof = db._query(`
  FOR v, e, p IN 1..2 OUTBOUND 'people/alice' GRAPH 'social'
    OPTIONS {uniqueVertices: 'global', order: 'bfs'}
    SORT v._key
    RETURN {name: v.name, hops: LENGTH(p.edges)}`).toArray();
fof.forEach(r => print('  ' + JSON.stringify(r)));
assert(JSON.stringify(fof.map(r => r.name)) === '["bob","carol","dave"]', 'alice reaches bob, carol (1 hop) and dave (2 hops)');

const path = db._query(`
  FOR v IN OUTBOUND SHORTEST_PATH 'people/alice' TO 'people/frank' GRAPH 'social' RETURN v._key`).toArray();
print('  shortest path alice -> frank: ' + path.join(' -> '));
assert(path.length === 5 && path[0] === 'alice' && path[4] === 'frank', 'a 4-hop shortest path');
print('ok: traversal and shortest path over sharded collections');
