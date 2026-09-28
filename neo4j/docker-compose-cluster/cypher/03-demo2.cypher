// A second user database (Enterprise only), replicated on all three servers;
// WAIT returns once the allocations report back, cluster.sh then polls until all 3 are online.
CREATE DATABASE demo2 IF NOT EXISTS TOPOLOGY 3 PRIMARIES WAIT 60 SECONDS;
