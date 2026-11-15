package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func verifyDurability() map[string]any {
	out := map[string]any{}
	if enabled("starrocks") {
		configs := rows("starrocks", "SELECT BE_ID,NAME,VALUE FROM information_schema.be_configs WHERE NAME='sync_tablet_meta'")
		backends, err := srMetadata("SHOW BACKENDS")
		check(err)
		if len(configs) != len(backends) || len(configs) < srCopies() {
			panic("missing native backend durability settings")
		}
		for _, row := range configs {
			if len(row) != 3 || row[2] != "true" {
				panic(fmt.Sprintf("tablet metadata synchronization is disabled: %v", row))
			}
		}
		out["starrocks_sync_tablet_meta"] = configs

		tables := rows("starrocks", "SELECT TABLE_NAME,PROPERTIES FROM information_schema.tables_config WHERE TABLE_SCHEMA='orders_lab' AND TABLE_NAME IN ('orders','customers','recovery')")
		if len(tables) != 3 {
			panic("missing native table configuration")
		}
		for _, row := range tables {
			if len(row) != 2 {
				panic("invalid table configuration schema")
			}
			var properties map[string]string
			check(json.Unmarshal([]byte(row[1]), &properties))
			// 4.1.7 TableProperty.buildWriteQuorum defaults an omitted property to
			// MAJORITY; SHOW CREATE also omits this default. Reject every override.
			quorum := properties["write_quorum"]
			if quorum != "" && quorum != "MAJORITY" {
				panic("StarRocks table must retain MAJORITY write quorum")
			}
			if properties["replication_num"] != fmt.Sprint(srCopies()) {
				panic("unexpected StarRocks replication factor")
			}
		}
		out["starrocks_table_properties"] = tables
	}
	if enabled("clickhouse") {
		old := os.Getenv("CH_HOST")
		defer func() { check(os.Setenv("CH_HOST", old)) }()
		settings := map[string]string{}
		for shard := 0; shard < chShards(); shard++ {
			for replica := 0; replica < chCopies(); replica++ {
				host := fmt.Sprintf("ch%d%d", shard, replica)
				check(os.Setenv("CH_HOST", host))
				for _, table := range []string{"orders_local", "customers_local", "recovery_local"} {
					ddl := rows("clickhouse", "SHOW CREATE TABLE orders_lab."+table)
					if len(ddl) != 1 || len(ddl[0]) != 1 {
						panic("missing ClickHouse table definition")
					}
					text := ddl[0][0]
					for _, flag := range []string{"fsync_after_insert", "fsync_part_directory"} {
						if !strings.Contains(text, flag+" = 1") && !strings.Contains(text, flag+" = true") {
							panic("ClickHouse table must fsync inserted parts and directory changes")
						}
					}
					settings[host+"/"+table] = text
				}
			}
		}
		out["clickhouse_fsync_tables"] = settings
	}
	return out
}
