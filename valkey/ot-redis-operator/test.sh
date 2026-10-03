# runs inside valkey-leader-0; $1 = spec.clusterSize
info=$(valkey-cli cluster info | tr -d '\r')
echo "$info" | grep -E '^cluster_(state|slots_ok|known_nodes|size):'
echo "$info" | grep -qx cluster_state:ok
echo "$info" | grep -qx cluster_slots_ok:16384

nodes=$(valkey-cli cluster nodes | grep -v fail)
masters=$(echo "$nodes" | grep -c master)
replicas=$(echo "$nodes" | grep -c slave)
echo "$masters masters, $replicas replicas"
[ "$masters" = "$1" ] && [ "$replicas" = "$1" ]

for i in $(seq 30); do valkey-cli -c set key:$i value-$i >/dev/null; done
for i in $(seq 30); do
	[ "$(valkey-cli -c get key:$i)" = value-$i ] || { echo "key:$i missing"; exit 1; }
done
echo "30/30 keys read back"
valkey-cli --cluster info localhost:6379 | grep -- '->'
