/ bench.q: KDB-X single-node benchmark. Runs in a client q process; the work runs on the server
/ (functions are sent over IPC and timed there with .z.n), except the IPC round trip and ingest,
/ which are timed here. N trades (default 10M, SMOKE=1: 1M) and 2N quotes over 100 symbols.
/ Prints a table and writes /results/kdbx-single-<UTC time>.json.
system "c 40 200";
env:{$[count v:getenv x;v;y]};
n:"J"$env[`N;$["1"~getenv`SMOKE;"1000000";"10000000"]];
h:hopen `$":",env[`KDBX_HOST;"localhost"],":5000";
srv:{[name;val] h(set;name;val)};
res:([] metric:`symbol$(); ms:`float$(); rows:`long$(); rows_per_s:`long$());
rec:{[m;ms;r] res::res,([] metric:enlist m; ms:enlist ms; rows:enlist r; rows_per_s:enlist $[ms>0;`long$r%ms%1000;0N]);
  -1 (string m),": ",(string .01*`long$100*ms)," ms",$[r>0;", ",(string `long$r%ms%1000)," rows/s";""]};
/ server-side: average ms of k runs of the q expression e (a string)
srv[`tm; {[k;e] t0:.z.n; do[k; value e]; 1e-6*(`long$.z.n-t0)%k}];
tm:{[k;e] h(`tm;k;e)};

-1 "KDB-X ",(string h".z.K")," ",(string h".z.k"),", ",(string h"system\"s\"")," secondary threads; N=",string n;

/ ---- data: N trades, 2N quotes, sorted by time, 100 symbols with their own price levels
srv[`syms; `$"S",'string til 100];
srv[`px; {x!10+(count x)?500f}`$"S",'string til 100];
srv[`gen; {[n]
  s:n?syms;
  `trade set ([] time:asc 09:30:00.000+n?23400000; sym:s; price:px[s]+0.01*floor 100*n?1f; size:100*1+n?10);
  qs:(2*n)?syms;
  `quote set ([] time:asc 09:30:00.000+(2*n)?23400000; sym:qs; bid:px[qs]-0.01*1+(2*n)?5; ask:px[qs]+0.01*1+(2*n)?5);
 }];
h"system \"S 42\"";
rec[`generate_trades_and_quotes; tm[1;"gen ",string n]; 3*n];

/ ---- IPC ingest: the client sends 100k-row batches; the server appends them to an empty table
b:100000;
nb:max 1,n div b;
batch:([] time:asc 09:30:00.000+b?23400000; sym:b?`$"S",'string til 100; price:10+b?500f; size:100*1+b?10);
schema:"ticks:([] time:`time$(); sym:`symbol$(); price:`float$(); size:`long$())";
h schema,"; ins:{x insert y;}";
t0:.z.n; do[nb; h(`ins;`ticks;batch)]; ms:1e-6*`long$.z.n-t0;
rec[`ipc_insert_sync_100k_batches; ms; nb*b];
h schema;
t0:.z.n; do[nb; neg[h](`ins;`ticks;batch)]; h"1"; ms:1e-6*`long$.z.n-t0;
rec[`ipc_insert_async_100k_batches; ms; nb*b];
h"delete ticks from `.";

/ ---- IPC round trip: 10k small sync queries from one client
lat:{t0:.z.n; h"1+1"; 1e-6*`long$.z.n-t0} each til 10000;
rec[`ipc_roundtrip_10k_queries; sum lat; 10000];
p:{[l;q] (asc l) floor q*count l};
-1 "  round trip p50 / p99 ms: ",(string p[lat;.5])," / ",string p[lat;.99];

/ ---- queries over all N trades (server-side, average of 5 runs)
rec[`select_by_sym_count_sum_vwap; tm[5;"select n:count i, v:sum size, vwap:size wavg price by sym from trade"]; n];
rec[`ohlc_1min_bars_all_syms; tm[5;"select o:first price, h:max price, l:min price, c:last price, v:sum size by sym, 1 xbar time.minute from trade"]; n];
rec[`vector_sum_price_x_size; tm[5;"exec sum price*size from trade"]; n];
rec[`filter_one_sym_no_attr; tm[10;"select from trade where sym=`S7"]; n];
h"update `g#sym from `trade; update `g#sym from `quote";
rec[`filter_one_sym_g_attr; tm[10;"select from trade where sym=`S7"]; n];

/ ---- as-of join: each trade gets the prevailing quote (quote sym has g#)
rec[`aj_trades_to_quotes; tm[1;"taq:aj[`sym`time;trade;quote]"]; n];
h"delete taq from `.";

/ ---- window join: max ask / min bid / quote count in the 1 s before each of the first 1M trades
w:min n,1000000;
h"qp:update `p#sym from `sym`time xasc quote; t1:",(string w),"#trade";
rec[`wj_1s_window_1m_trades; tm[1;"wj[-1000 0+\\:t1`time;`sym`time;t1;(qp;(max;`ask);(min;`bid);(count;`bid))]"]; w];
h"delete qp, t1 from `.";

/ ---- write the trades as one date partition (.Q.dpft: sort by sym, p#, enumerate), then query it on disk
h"system \"rm -rf /data/bench\"; trades:trade";
rec[`write_partition_dpft; tm[1;".Q.dpft[`:/data/bench;2026.10.02;`sym;`trades]"]; n];
mb:h"1e-6*sum hcount each ` sv' p,'key p:`:/data/bench/2026.10.02/trades";
-1 "  partition size: ",(string mb)," MB";
h"delete trades from `.; system \"l /data/bench\"";
rec[`hdb_vwap_by_sym_one_date; tm[5;"select vwap:size wavg price by sym from trades where date=2026.10.02"]; n];
rec[`hdb_one_sym_one_date_p_attr; tm[10;"select from trades where date=2026.10.02, sym=`S7"]; n];

mem:h".Q.w[]";
-1 "  server memory used / heap: ",(string `long$1e-6*mem`used)," / ",(string `long$1e-6*mem`heap)," MB";
/ free the in-memory data; the server keeps /data/bench mapped as its cwd until the next \l
h"delete trade, quote, ins, gen from `.; .Q.gc[]";

show res;
ts:ssr[;":";""] 19#string .z.p;
out:`timestamp`server`n`partition_mb`server_mem`ipc_p50_ms`ipc_p99_ms`limits`docker`results!(
  ts; h"(`version`release`os`threads!(.z.K;.z.k;.z.o;system\"s\"))"; n; mb; mem; p[lat;.5]; p[lat;.99];
  @[.j.k;getenv`BENCH_LIMITS;{()}]; getenv`DOCKER_INFO; res);
f:`$":/results/kdbx-single-",ts,".json";
f 0: enlist .j.j out;
-1 "results: ",1_string f;
exit 0
