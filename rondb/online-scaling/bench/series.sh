#!/bin/sh
# series.sh RAW EVENTS: sysbench's 5 s interval reports from RAW (results/<label>.txt, written by
# bench/sb.sh) as a table, with the "<epoch> <text>" lines from EVENTS placed at the interval
# they happened in.
raw=$1 events=$2
awk -v events="$events" '
	BEGIN { n = 0; while ((getline line < events) > 0) { split(line, a, " "); ev_t[n] = a[1];
		sub(/^[0-9]+ /, "", line); ev_s[n++] = line } }
	/^start_epoch=/ { start = substr($0, 13); next }
	/^\[ *[0-9]+s \]/ {
		t = $2; sub(/s/, "", t)
		for (i = 0; i < n; i++) if (!done[i] && ev_t[i] - start <= t) {
			printf "            >>> %4ds %s\n", ev_t[i] - start, ev_s[i]; done[i] = 1 }
		match($0, /tps: [0-9.]+/); tps = substr($0, RSTART + 5, RLENGTH - 5)
		match($0, /lat \(ms,99%\): [0-9.]+/); p99 = substr($0, RSTART + 14, RLENGTH - 14)
		match($0, /err\/s: [0-9.]+/); err = substr($0, RSTART + 7, RLENGTH - 7)
		printf "%5ss  tps %8.1f  p99 %8.2f ms  err/s %6.2f\n", t, tps, p99, err
	}
	END { for (i = 0; i < n; i++) if (!done[i]) printf "            >>> %4ds %s\n", ev_t[i] - start, ev_s[i] }
' "$raw"
