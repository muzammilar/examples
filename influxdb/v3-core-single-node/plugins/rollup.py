"""Schedule trigger: every run aggregates the last `window` of `home` per room with SQL and
writes the result to `home_rollup` (a downsampling task inside the database).
Trigger: influxdb3 create trigger --trigger-spec every:5s --path rollup.py
         --trigger-arguments window=1h ..."""


def process_scheduled_call(influxdb3_local, call_time, args=None):
    window = (args or {}).get("window", "1h")
    rows = influxdb3_local.query(
        "SELECT room, avg(temp) AS avg_temp, max(temp) AS max_temp, avg(hum) AS avg_hum, "
        "count(*) AS readings FROM home "
        f"WHERE time >= now() - INTERVAL '{window}' GROUP BY room"
    )
    for r in rows:
        line = LineBuilder("home_rollup")
        line.tag("room", r["room"])
        line.tag("window", window)
        line.float64_field("avg_temp", r["avg_temp"])
        line.float64_field("max_temp", r["max_temp"])
        line.float64_field("avg_hum", r["avg_hum"])
        line.int64_field("readings", r["readings"])
        influxdb3_local.write(line)
    influxdb3_local.info(f"rollup: {len(rows)} room(s) over the last {window} at {call_time}")
