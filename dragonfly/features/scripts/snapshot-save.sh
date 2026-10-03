# Snapshot in Dragonfly's own format (.dfs): a summary file plus one file per shard, written in parallel
c() { valkey-cli -h dragonfly "$@"; }

c SET snap:string 'still here' > /dev/null
c HSET snap:hash a 1 b 2 > /dev/null
c JSON.SET snap:json '$' '{"saved":true}' > /dev/null
echo "keys before SAVE: $(c DBSIZE)"
r=$(c SAVE)
echo "SAVE -> $r"
[ "$r" = OK ] || { echo "FAIL: SAVE did not succeed"; exit 1; }
echo "BGSAVE -> $(c BGSAVE)"
# "saving" tracks the running save; rdb_bgsave_in_progress stays 1 after a BGSAVE in v2.0.0
for i in $(seq 30); do
	c INFO persistence | tr -d '\r' | grep -qx saving:0 && break
	sleep 1
done
c INFO persistence | grep -E '^(saving|last_success_save|last_saved_file|rdb_last_bgsave_status|rdb_changes_since_last_success_save):'
ls -l /data
[ "$(c INFO persistence | tr -d '\r' | grep '^last_saved_file:')" = last_saved_file:/data/dump-summary.dfs ] &&
	[ -f /data/dump-summary.dfs ] || { echo "FAIL: no /data/dump-summary.dfs"; exit 1; }
