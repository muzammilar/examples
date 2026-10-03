// Capital markets: a C++ client of one kdb+ q process, using KX's C API (k.h + c.o).
//
// 1. simulates one trading day (09:30-16:00) for SYMS symbols in C++: a stream of quotes (random
//    walk mid, 1-5 cent spread) and trades that hit the current bid or ask or print at the mid,
//    and streams both to q as BATCH-row column tables over IPC (insert);
// 2. runs the analytics on the server, timed from the client: VWAP, OHLCV bars, as-of join of
//    every trade to its prevailing quote, trade classification, effective spread, window join,
//    realized volatility, a full-column vector scan;
// 3. checks q's answers against what the client knows from generating the data: per-symbol
//    count, volume, notional, and how many trades were buys (at the ask), sells (at the bid) and
//    at the mid, which the aj must reproduce exactly. Exits 1 on a mismatch.
//
// env: KDB_HOST (kdb), KDB_PORT (5000), TRADES (20000000), QUOTES_PER_TRADE (2), SYMS (100),
//      BATCH (1000000), SEED (42)
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

#include "k.h"
// k.h defines short macros (O, R, U, Z, P, ...) that clash with ordinary C++ names
#undef O
#undef R
#undef Z
#undef P
#undef U
#undef SW
#undef CS
#undef CD
#undef DO

namespace {

using Clock = std::chrono::steady_clock;

std::string env(const char* name, const char* dflt) {
	const char* v = std::getenv(name);
	return v && *v ? v : dflt;
}
long long envll(const char* name, long long dflt) {
	const char* v = std::getenv(name);
	return v && *v ? std::atoll(v) : dflt;
}
double ms_since(Clock::time_point t0) {
	return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

int h = 0; // IPC handle

// drop this program's globals on the server (functional delete from the root namespace)
const char* DROP = "![`.;();0b;(`trade`quote`taq`bars`es`rv`wjr) inter key `.]; .Q.gc[]";

// run q code on the server and return the result; dies on a q error (type -128)
K q(const std::string& code) {
	K r = k(h, const_cast<S>(code.c_str()), (K)0);
	if (!r) {
		std::fprintf(stderr, "connection lost running: %s\n", code.c_str());
		std::exit(2);
	}
	if (r->t == -128) {
		std::fprintf(stderr, "q error '%s running: %s\n", r->s, code.c_str());
		std::exit(2);
	}
	return r;
}
void qrun(const std::string& code) { r0(q(code)); }
// q formats the value (.Q.s): what the q console would show
void show(const std::string& code) {
	K r = q(".Q.s " + code);
	std::printf("%.*s", (int)r->n, (char*)kC(r));
	r0(r);
}

struct Timing {
	std::string name;
	double ms;
	long long rows;
};
std::vector<Timing> timings;
// time q code from the client (round trip, including sending back a small result)
K timed(const std::string& name, long long rows, const std::string& code) {
	auto t0 = Clock::now();
	K r = q(code);
	double ms = ms_since(t0);
	timings.push_back({name, ms, rows});
	std::printf("  %-34s %10.1f ms", name.c_str(), ms);
	if (rows > 0) std::printf("  %8.1f M rows/s", rows / ms / 1e3);
	std::printf("\n");
	return r;
}

// a column of a (non-keyed) table result
K col(K table, int i) { return kK(kK(table->k)[1])[i]; }

// growing column buffers for one table, turned into a q table (flip of a dict) for insert
struct TradeBatch {
	std::vector<J> time, size;
	std::vector<S> sym;
	std::vector<F> price;
	size_t n() const { return time.size(); }
	void clear() { time.clear(), size.clear(), sym.clear(), price.clear(); }
};
struct QuoteBatch {
	std::vector<J> time, bsize, asize;
	std::vector<S> sym;
	std::vector<F> bid, ask;
	size_t n() const { return time.size(); }
	void clear() { time.clear(), bsize.clear(), asize.clear(), sym.clear(), bid.clear(), ask.clear(); }
};

K vecJ(int type, const std::vector<J>& v) {
	K x = ktn(type, (J)v.size());
	std::memcpy(kJ(x), v.data(), v.size() * sizeof(J));
	return x;
}
K vecF(const std::vector<F>& v) {
	K x = ktn(KF, (J)v.size());
	std::memcpy(kF(x), v.data(), v.size() * sizeof(F));
	return x;
}
K vecS(const std::vector<S>& v) { // symbols are interned pointers from ss()
	K x = ktn(KS, (J)v.size());
	std::memcpy(kS(x), v.data(), v.size() * sizeof(S));
	return x;
}
K names(std::initializer_list<const char*> cols) {
	K x = ktn(KS, (J)cols.size());
	J i = 0;
	for (const char* c : cols) kS(x)[i++] = ss(const_cast<S>(c));
	return x;
}

// `table insert batch`; k() takes ownership of its K arguments
void insert(const char* table, K columns_names, K columns) {
	K r = k(h, const_cast<S>("insert"), ks(const_cast<S>(table)), xT(xD(columns_names, columns)), (K)0);
	if (!r || r->t == -128) {
		std::fprintf(stderr, "insert into %s failed: %s\n", table, r ? r->s : "connection lost");
		std::exit(2);
	}
	r0(r);
}
void flush(TradeBatch& b) {
	if (!b.n()) return;
	insert("trade", names({"time", "sym", "price", "size"}),
	       knk(4, vecJ(KP, b.time), vecS(b.sym), vecF(b.price), vecJ(KJ, b.size)));
	b.clear();
}
void flush(QuoteBatch& b) {
	if (!b.n()) return;
	insert("quote", names({"time", "sym", "bid", "ask", "bsize", "asize"}),
	       knk(6, vecJ(KP, b.time), vecS(b.sym), vecF(b.bid), vecF(b.ask), vecJ(KJ, b.bsize), vecJ(KJ, b.asize)));
	b.clear();
}

// what the client knows about each symbol's trades
struct Truth {
	long long trades = 0, volume = 0, buys = 0, sells = 0, mids = 0;
	double notional = 0;
};

} // namespace

int main() {
	const std::string host = env("KDB_HOST", "kdb");
	const int port = (int)envll("KDB_PORT", 5000);
	const long long ntrades = envll("TRADES", 20000000);
	const long long qpt = envll("QUOTES_PER_TRADE", 2);
	const int nsyms = (int)envll("SYMS", 100);
	const size_t batch = (size_t)envll("BATCH", 1000000);
	const unsigned long long seed = (unsigned long long)envll("SEED", 42);

	// capability 1: messages over 2 GB allowed; no timeout
	h = khpunc(const_cast<S>(host.c_str()), port, const_cast<S>(""), 0, 1);
	if (h <= 0) {
		std::fprintf(stderr, "cannot connect to %s:%d (%d)\n", host.c_str(), port, h);
		return 2;
	}
	std::printf("connected to %s:%d: ", host.c_str(), port);
	show("`version`release`threads!(.z.K;.z.k;system\"s\")");

	// ---- 1. simulate the day and stream it to q
	const long long nevents = ntrades * (1 + qpt);
	std::printf("\n1. simulate %lld trades + %lld quotes for %d symbols, insert in %zu-row batches\n",
	            ntrades, ntrades * qpt, nsyms, batch);
	qrun(DROP);
	qrun("trade:([] time:`timestamp$(); sym:`symbol$(); price:`float$(); size:`long$())");
	qrun("quote:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$(); bsize:`long$(); asize:`long$())");

	std::mt19937_64 rng(seed);
	std::uniform_real_distribution<double> unif(0, 1);
	std::normal_distribution<double> gauss(0, 1);
	// symbols S000..; activity is Zipf-like (symbol i has weight 1/(i+1)); prices 10..510
	std::vector<S> syms(nsyms);
	std::vector<double> mid(nsyms), weight(nsyms);
	std::vector<int> spread(nsyms, 1); // cents
	for (int i = 0; i < nsyms; i++) {
		char name[16];
		std::snprintf(name, sizeof name, "S%03d", i);
		syms[i] = ss(name);
		mid[i] = std::round((10 + 500 * unif(rng)) * 100) / 100;
		weight[i] = 1.0 / (i + 1);
	}
	std::discrete_distribution<int> pick(weight.begin(), weight.end());
	std::vector<Truth> truth(nsyms);

	// timestamps: nanoseconds since 2000.01.01; 2026.10.02 09:30 + i * (6.5 h / events), strictly increasing
	const J day0 = (J)ymd(2026, 10, 2) * 86400000000000LL + 34200000000000LL;
	const double step = 23400e9 / (double)nevents;

	TradeBatch tb;
	QuoteBatch qb;
	tb.time.reserve(batch), qb.time.reserve(batch);
	// an opening quote for every symbol just before 09:30, so every trade has a prevailing quote
	for (int i = 0; i < nsyms; i++) {
		qb.time.push_back(day0 - nsyms + i), qb.sym.push_back(syms[i]);
		qb.bid.push_back(mid[i] - 0.005), qb.ask.push_back(mid[i] + 0.005), qb.bsize.push_back(100), qb.asize.push_back(100);
	}
	auto t0 = Clock::now();
	double gen_ms = 0, send_ms = 0;
	for (long long e = 0; e < nevents; e++) {
		const int s = pick(rng);
		const J t = day0 + (J)(e * step);
		const double half = spread[s] / 200.0;
		if (unif(rng) * (1 + qpt) >= 1) { // quote: the mid moves, a new spread
			mid[s] = std::max(1.0, std::round((mid[s] + 0.01 * gauss(rng)) * 200) / 200);
			spread[s] = 1 + (int)(5 * unif(rng));
			const double hs = spread[s] / 200.0;
			qb.time.push_back(t), qb.sym.push_back(syms[s]);
			qb.bid.push_back(mid[s] - hs), qb.ask.push_back(mid[s] + hs);
			qb.bsize.push_back(100 * (1 + (J)(20 * unif(rng)))), qb.asize.push_back(100 * (1 + (J)(20 * unif(rng))));
			if (qb.n() == batch) {
				auto s0 = Clock::now();
				flush(qb);
				send_ms += ms_since(s0);
			}
		} else { // trade at the ask (buy), the bid (sell) or the mid
			const double u = unif(rng);
			double price;
			Truth& tr = truth[s];
			if (u < 0.45) price = mid[s] + half, tr.buys++;
			else if (u < 0.9) price = mid[s] - half, tr.sells++;
			else price = mid[s], tr.mids++;
			const J size = 100 * (1 + (J)(10 * unif(rng)));
			tr.trades++, tr.volume += size, tr.notional += price * size;
			tb.time.push_back(t), tb.sym.push_back(syms[s]), tb.price.push_back(price), tb.size.push_back(size);
			if (tb.n() == batch) {
				auto s0 = Clock::now();
				flush(tb);
				send_ms += ms_since(s0);
			}
		}
	}
	{
		auto s0 = Clock::now();
		flush(tb), flush(qb);
		send_ms += ms_since(s0);
	}
	gen_ms = ms_since(t0) - send_ms;
	timings.push_back({"generate in C++", gen_ms, nevents});
	timings.push_back({"insert over IPC", send_ms, nevents});
	std::printf("  %-34s %10.1f ms  %8.1f M rows/s\n", "generate in C++", gen_ms, nevents / gen_ms / 1e3);
	std::printf("  %-34s %10.1f ms  %8.1f M rows/s\n", "insert over IPC", send_ms, nevents / send_ms / 1e3);
	show("`trade`quote!count each (trade;quote)");
	show("`used`heap#`long$1e-6*.Q.w[]");  // MB
	std::printf("first quotes and trades:\n");
	show("3#quote");
	show("3#trade");

	// ---- 2. analytics on the server
	std::printf("\n2. analytics (timed at the client, server-side q)\n");
	r0(timed("g# on quote sym (index build)", ntrades * qpt, "update `g#sym from `quote; count quote"));

	K v = timed("VWAP, volume, notional by sym", ntrades,
	            "0!select n:count i, volume:sum size, notional:sum price*size, vwap:size wavg price by sym from trade");
	r0(timed("1-min OHLCV bars, all syms", ntrades,
	         "count bars:0!select open:first price, high:max price, low:min price, close:last price, volume:sum size "
	         "by sym, bar:1 xbar time.minute from trade"));
	r0(timed("5-min VWAP bars, all syms", ntrades, "count select vwap:size wavg price by sym, 5 xbar time.minute from trade"));
	r0(timed("aj: every trade -> prevailing quote", ntrades, "count taq:aj[`sym`time; trade; quote]"));
	K cls = timed("classify trades (bid/ask/mid)", ntrades,
	              "0!select buys:`long$sum price>=ask, sells:`long$sum price<=bid, mids:`long$sum (price>bid)&price<ask by sym from taq");
	r0(timed("effective spread (bps) by sym", ntrades,
	         "count es:0!select espread_bps:avg 2e4*abs[price-m]%m by sym from update m:0.5*bid+ask from taq"));
	r0(timed("realized vol from 1-min closes", 0,
	         "count rv:0!select rv_pct:100*sqrt[390]*dev 1_ log ratios close by sym from bars"));
	r0(timed("wj: 1 s max ask / min bid, S000", 0,
	         "count wjr:{wj[-1000000000 0+\\:x`time; `sym`time; x; (select from quote where sym=`S000; "
	         "(max;`ask); (min;`bid); (count;`bid))]} select from trade where sym=`S000"));
	r0(timed("vector scan: sum price*size", ntrades, "exec sum price*size from trade"));

	std::printf("\ntop 5 symbols by volume (VWAP from q):\n");
	show("5#`volume xdesc select sym, n, volume, vwap from (0!select n:count i, volume:sum size, vwap:size wavg price by sym from trade)");
	std::printf("1-min bars, S000, first 3:\n");
	show("3#select from bars where sym=`S000");
	std::printf("last 3 S000 trades with their prevailing quote (aj):\n");
	show("-3#select from taq where sym=`S000");
	std::printf("window join for the last 3 S000 trades (quotes in the preceding 1 s):\n");
	show("-3#wjr");
	std::printf("widest effective spreads and highest realized vol:\n");
	show("3#`espread_bps xdesc es");
	show("3#`rv_pct xdesc rv");

	// ---- 3. check q's answers against the client's own bookkeeping
	std::printf("\n3. verify against the client-side truth\n");
	int bad = 0;
	double worst = 0;
	auto index_of = [&](S s) {
		for (int i = 0; i < nsyms; i++)
			if (syms[i] == s) return i; // interned: equal symbols have equal pointers
		return -1;
	};
	for (J r = 0; r < col(v, 0)->n; r++) {
		const int i = index_of(kS(col(v, 0))[r]);
		if (i < 0) { bad++; continue; }
		const Truth& t = truth[i];
		const double rel = std::fabs(kF(col(v, 3))[r] - t.notional) / t.notional;
		worst = std::max(worst, rel);
		if (kJ(col(v, 1))[r] != t.trades || kJ(col(v, 2))[r] != t.volume || rel > 1e-9) bad++;
	}
	std::printf("  count, volume per sym exact, notional within %.1e (max rel diff): %s\n", worst, bad ? "MISMATCH" : "ok");
	int badc = 0;
	for (J r = 0; r < col(cls, 0)->n; r++) {
		const int i = index_of(kS(col(cls, 0))[r]);
		if (i < 0) { badc++; continue; }
		const Truth& t = truth[i];
		// cast to long (KJ) in the query
		if (kJ(col(cls, 1))[r] != t.buys || kJ(col(cls, 2))[r] != t.sells || kJ(col(cls, 3))[r] != t.mids) badc++;
	}
	std::printf("  aj classification (buys at ask, sells at bid, at mid) per sym: %s\n", badc ? "MISMATCH" : "ok");
	r0(v), r0(cls);

	std::printf("\ntimings\n");
	for (const auto& t : timings) {
		std::printf("  %-34s %10.1f ms", t.name.c_str(), t.ms);
		if (t.rows > 0) std::printf("  %8.1f M rows/s", t.rows / t.ms / 1e3);
		std::printf("\n");
	}
	show("`used`heap`peak#`long$1e-6*.Q.w[]");  // MB
	qrun(DROP);
	kclose(h);
	if (bad || badc) {
		std::printf("\nVERIFY FAILED\n");
		return 1;
	}
	std::printf("\nVERIFY PASSED\n");
	return 0;
}
