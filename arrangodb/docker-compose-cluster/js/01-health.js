// Cluster health from the agency's supervision: every member GOOD, one agency leader
const {assert, health, printHealth} = require('/js/lib.js');
printHealth();
const h = health();
const count = role => h.filter(s => s.role === role && s.status === 'GOOD').length;
assert(count('Agent') === 3, '3 GOOD agents');
assert(count('DBServer') === 3, '3 GOOD DB-servers');
assert(count('Coordinator') === 2, '2 GOOD coordinators');
assert(h.filter(s => s.leading).length === 1, 'exactly one agency leader');
print('ok: 3 agents, 3 DB-servers, 2 coordinators GOOD');
