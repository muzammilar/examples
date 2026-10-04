-- On the leader: election state and the replication links to the two followers.
assert(box.info.election.state == 'leader', 'not the leader: ' .. box.info.election.state)
local peers = {}
for _, r in pairs(box.info.replication) do
    if r.name ~= box.info.name then
        peers[r.name] = {
            upstream = r.upstream and r.upstream.status or 'none',
            downstream = r.downstream and r.downstream.status or 'none',
            lag_s = r.upstream and r.upstream.lag or 0,
        }
    end
end
return {
    leader = box.info.name,
    term = box.info.election.term,
    synchro_quorum = box.cfg.replication_synchro_quorum,
    failover = 'election (election_mode=' .. box.cfg.election_mode .. ')',
    peers = peers,
}
