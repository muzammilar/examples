// helpers shared by js/*.js and failover/*.js (run in arangosh on coordinator-1)
exports.assert = function (ok, msg) {
  if (!ok) { throw new Error('FAIL: ' + msg); }
};

// GET that throws on an error response
exports.get = function (path) {
  const r = arango.GET(path);
  if (r.error) { throw new Error(path + ': ' + JSON.stringify(r)); }
  return r;
};

// /_admin/cluster/health as [{id, role, host, shortName, status, leading}], host = container hostname
// (empty while a server is FAILED: its endpoint is gone, the short name stays)
exports.health = function () {
  const h = exports.get('/_admin/cluster/health').Health;
  return Object.keys(h).map(id => ({
    id, role: h[id].Role, status: h[id].Status, shortName: h[id].ShortName || null,
    host: h[id].Endpoint.replace(/^tcp:\/\//, '').replace(/:\d+$/, ''),
    leading: h[id].Leading === true,
  })).sort((a, b) => (a.role + a.host).localeCompare(b.role + b.host));
};

// shard -> {leader, followers, inSync} for one collection, servers as container hostnames
// (the short name for a FAILED server);
// inSync: Current (what the DB-servers report) matches Plan (what the agency wants)
exports.shards = function (dbName, coll) {
  const byShort = {}; // shardDistribution names servers by short name, or by id once FAILED
  exports.health().forEach(s => { byShort[s.shortName] = byShort[s.id] = s.host || s.shortName; });
  const d = exports.get('/_db/' + dbName + '/_admin/cluster/shardDistribution').results[coll];
  const out = {};
  Object.keys(d.Plan).sort().forEach(shard => {
    const p = d.Plan[shard], c = d.Current[shard] || {leader: null, followers: []};
    out[shard] = {
      leader: byShort[c.leader] || c.leader,
      followers: c.followers.map(f => byShort[f] || f),
      inSync: p.leader === c.leader && JSON.stringify(p.followers.slice().sort()) === JSON.stringify(c.followers.slice().sort()),
    };
  });
  return out;
};

// wait up to `seconds` for fn() to return truthy; returns the elapsed seconds
exports.waitFor = function (what, seconds, fn) {
  const start = Date.now();
  while (true) {
    let ok = false;
    try { ok = fn(); } catch (e) { ok = false; }
    if (ok) { return Math.round((Date.now() - start) / 1000); }
    exports.assert((Date.now() - start) / 1000 < seconds, what + ' within ' + seconds + ' s');
    require('internal').sleep(1);
  }
};

exports.printHealth = function () {
  exports.health().forEach(s => print('  ' + [s.role.padEnd(11), (s.host || '-').padEnd(13), (s.shortName || '').padEnd(15),
    s.status, s.leading ? '(agency leader)' : ''].join(' ').trimEnd()));
};
