/ ==== 1. In-memory tables: one day of trades and quotes for five symbols (seeded)
system "S 42"
n:1000000
syms:`AAPL`MSFT`GOOG`AMZN`NVDA
base:syms!190 420 165 185 120f
s:n?syms
/ 09:30 plus up to 6.5 h in ms; asc returns the list with the s# (sorted) attribute
trade:([] time:asc 09:30:00.000+n?23400000; sym:s; price:base[s]+0.01*floor 100*n?1f; size:100*1+n?10)
qs:(2*n)?syms
quote:([] time:asc 09:30:00.000+(2*n)?23400000; sym:qs; bid:base[qs]-0.01*1+(2*n)?5; ask:base[qs]+0.01*1+(2*n)?5)
count each (trade;quote)
meta trade
5#trade
/ keyed table (reference data) and an upsert by key
ref:([sym:syms] name:("Apple";"Microsoft";"Alphabet";"Amazon";"NVIDIA"); sector:`tech`tech`tech`retail`semis)
`ref upsert (`TSLA;"Tesla";`auto)
ref

/ ==== 2. q-sql: select / exec / update / by / left join
select from trade where sym=`AAPL, size>=900, time within 10:00:00.000 10:01:00.000
select trades:count i, volume:sum size, vwap:size wavg price, hi:max price, lo:min price by sym from trade
exec avg price by sym from trade
/ one-minute OHLC bars: xbar buckets the time
3#select open:first price, high:max price, low:min price, close:last price, volume:sum size by sym, bar:1 xbar time.minute from trade where sym=`NVDA
update mid:0.5*bid+ask, spread:ask-bid from `quote
select avg spread by sym from quote
3#trade lj ref

/ ==== 3. As-of join: for each trade, the prevailing quote of the same sym at or before its time
system "t aj[`sym`time; trade; quote]"
/ aj looks quotes up by sym; g# (grouped: a hash index sym -> row positions) makes that a lookup
update `g#sym from `quote
system "t aj[`sym`time; trade; quote]"
taq:aj[`sym`time; trade; quote]
5#taq
/ trades at or above the ask (buyer-initiated) per sym
select buys:sum price>=ask, sells:sum price<=bid by sym from taq

/ ==== 4. Window join: the best ask and bid over the 2 s before each AAPL trade
/ wj wants the quote table sorted by sym, time and grouped or parted on sym
qp:update `p#sym from `sym`time xasc quote
t1:select from trade where sym=`AAPL
w:-2000 0+\:t1`time
5#wj[w; `sym`time; t1; (qp; (max;`ask); (min;`bid); (count;`bid))]

/ ==== 5. Attributes: s# sorted, g# grouped, p# parted
attr each (trade`time; quote`sym; qp`sym; trade`sym)
/ s#: = and within on a sorted column are binary searches instead of scans
st:([] t:asc 10000000?100000000; v:10000000?1f)
nt:update `#t from st
attr each (st`t; nt`t)
system "t:100 select from st where t within 50000000 50001000"
system "t:100 select from nt where t within 50000000 50001000"
/ g#: 10M rows, 17,576 random three-letter symbols; a sym lookup touches only its rows
gt:([] sym:10000000?`3; v:10000000?100f)
system "t:100 select sum v from gt where sym=`abc"
update `g#sym from `gt
system "t:100 select sum v from gt where sym=`abc"
/ p#: the list is in contiguous runs of each value (needs sorting by it); kept small, used on disk
attr (update `p#sym from `sym xasc trade)`sym

/ ==== 6. Splayed table on disk: a directory with one file per column; symbols enumerated
/ .Q.en writes /data/db/sym (the enumeration domain) and replaces symbols with indexes into it
`:/data/db/ref/ set .Q.en[`:/data/db] 0!ref
key `:/data/db/ref
get `:/data/db/ref/.d

/ ==== 7. Partitioned table on disk: /data/db/<date>/trades/, one splayed table per date
/ .Q.dpft sorts by sym, sets p# on sym, enumerates and writes one partition; three trading days
dates:2026.09.28 2026.09.29 2026.09.30
{trades::update price:price*1+0.01*x-2026.09.28 from trade; .Q.dpft[`:/data/db;x;`sym;`trades]} each dates
key `:/data/db
key `:/data/db/2026.09.29/trades
/ load the database: maps every table under /data/db (trades, ref) and the sym file; cwd becomes /data/db
system "l /data/db"
tables[]
meta trades
select rows:count i, vwap:size wavg price by date from trades
/ date (the virtual partition column) first in the where clause prunes partitions
select vwap:size wavg price, volume:sum size by sym from trades where date=2026.09.30
select from trades where date=2026.09.29, sym=`MSFT, time within 12:00:00.000 12:00:01.000
ref
