package main

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"net/url"
	"strconv"
	"time"
)

func srMetadata(text string) ([]map[string]string, error) {
	db, err := srDB()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	data, err := db.QueryContext(ctx, text)
	if err != nil {
		return nil, err
	}
	defer data.Close()
	columns, err := data.Columns()
	if err != nil {
		return nil, err
	}
	raw := make([]sql.RawBytes, len(columns))
	dest := make([]any, len(columns))
	for i := range raw {
		dest[i] = &raw[i]
	}
	var result []map[string]string
	for data.Next() {
		if err := data.Scan(dest...); err != nil {
			return nil, err
		}
		row := map[string]string{}
		for i, name := range columns {
			row[name] = string(raw[i])
		}
		result = append(result, row)
	}
	return result, data.Err()
}

func srReplicaChecksums(table string) (map[string]map[string]string, uint64) {
	backends, err := srMetadata("SHOW BACKENDS")
	check(err)
	hosts := map[string]string{}
	for _, row := range backends {
		if row["Alive"] == "true" {
			hosts[row["BackendId"]] = row["IP"]
		}
	}
	if len(hosts) < srCopies() {
		panic("expected at least three live native backends")
	}
	partitions, err := srMetadata("SHOW PARTITIONS FROM orders_lab." + table)
	check(err)
	if len(partitions) != 1 {
		panic("expected one logical orders partition")
	}
	version := uintValue(partitions[0]["VisibleVersion"])
	var tablets []map[string]string
	deadline := time.Now().Add(180 * time.Second)
	for time.Now().Before(deadline) {
		tablets, err = srMetadata("SHOW TABLET FROM orders_lab." + table)
		check(err)
		ok := len(tablets) == 3*srCopies()
		for _, row := range tablets {
			v, e := strconv.ParseUint(row["Version"], 10, 64)
			ok = ok && e == nil && v >= version && row["State"] == "NORMAL" && hosts[row["BackendId"]] != ""
		}
		if ok {
			break
		}
		time.Sleep(time.Second)
	}
	if len(tablets) != 3*srCopies() {
		panic("expected nine physical tablet replicas")
	}
	checksums := map[string]map[string]string{}
	for _, row := range tablets {
		if uintValue(row["Version"]) < version || row["State"] != "NORMAL" {
			panic("native tablet repair did not converge")
		}
		tablet := row["TabletId"]
		host := hosts[row["BackendId"]]
		address := "http://" + host + ":8040/api/checksum?" + url.Values{"tablet_id": {tablet}, "version": {strconv.FormatUint(version, 10)}}.Encode()
		response, err := httpRequest("GET", address, "root", "", nil, nil)
		check(err)
		body, err := io.ReadAll(response.Body)
		check(err)
		check(response.Body.Close())
		checksum := string(body)
		if _, err := strconv.ParseUint(checksum, 10, 32); err != nil {
			panic("invalid native replica checksum")
		}
		if checksums[tablet] == nil {
			checksums[tablet] = map[string]string{}
		}
		checksums[tablet][host] = checksum
	}
	if len(checksums) != 3 {
		panic("expected three native hash buckets")
	}
	for _, copies := range checksums {
		if len(copies) != srCopies() {
			panic("missing physical bucket copy")
		}
		expectedChecksum := ""
		for _, checksum := range copies {
			if expectedChecksum != "" && checksum != expectedChecksum {
				panic("replica checksum mismatch")
			}
			expectedChecksum = checksum
		}
	}
	save("starrocks-"+table+"-replicas", map[string]any{"visible_version": version, "replica_checksums": checksums})
	return checksums, version
}

func verifyStarRocksPlacement() {
	checksums, version := srReplicaChecksums("orders")
	seen := make([]bool, count())
	customers := map[uint32]string{}
	placement := map[string]int{}
	for tablet := range checksums {
		from := fmt.Sprintf("orders_lab.orders TABLET(%s)", tablet)
		check(readOrders("starrocks", from, len(seen)+1, func(values []string) error {
			row := parseOrder(values)
			if row.ID >= uint64(len(seen)) || seen[row.ID] || row != expected(row.ID, true) {
				return fmt.Errorf("wrong or duplicate tablet row %d", row.ID)
			}
			if previous, ok := customers[row.Customer]; ok && previous != tablet {
				return fmt.Errorf("customer %d spans hash buckets", row.Customer)
			}
			customers[row.Customer] = tablet
			seen[row.ID] = true
			placement[tablet]++
			return nil
		}))
	}
	for id, found := range seen {
		if !found {
			panic(fmt.Sprintf("missing native tablet row %d", id))
		}
	}
	save("starrocks-placement", map[string]any{"visible_version": version, "logical_buckets": len(placement), "replicas_per_bucket": srCopies(), "row_counts": placement, "replica_checksums": checksums, "missing_or_wrong": 0})
}

func nativeStatus() {
	front, err := srMetadata("SHOW FRONTENDS")
	check(err)
	back, err := srMetadata("SHOW BACKENDS")
	check(err)
	save("native-membership", map[string]any{"frontends": front, "backends": back})
}
