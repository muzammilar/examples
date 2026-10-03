"""WAL (data write) trigger: runs on every WAL flush (about once a second) with the rows just
written to `home`, and writes one `home_alerts` row per reading above the `max_temp` argument.
Trigger: influxdb3 create trigger --trigger-spec table:home --path temp_alert.py
         --trigger-arguments max_temp=23 ..."""


def process_writes(influxdb3_local, table_batches, args=None):
    max_temp = float((args or {}).get("max_temp", "23"))
    alerts = 0
    for batch in table_batches:
        if batch["table_name"] != "home":
            continue
        for row in batch["rows"]:
            temp = row.get("temp")
            if temp is None or temp <= max_temp:
                continue
            line = LineBuilder("home_alerts")  # provided by the engine, no import needed
            line.tag("room", row["room"])
            line.float64_field("temp", temp)
            line.float64_field("max_temp", max_temp)
            line.time_ns(row["time"])
            influxdb3_local.write(line)
            alerts += 1
    if alerts:
        influxdb3_local.info(f"temp_alert: {alerts} reading(s) above {max_temp}")
