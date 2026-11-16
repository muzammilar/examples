package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
)

type profileConfig struct {
	Version  int64 `json:"version"`
	Requests int   `json:"requests"`
}

func profileName(version int64, customer int) string {
	return fmt.Sprintf("profile-%d-%d", version, customer)
}

type profileLedger map[int]map[int64]byte

func profileHistory(root string, route []int) (profileLedger, error) {
	result := profileLedger{}
	paths, err := filepath.Glob(filepath.Join(root, "analytics", "*", "analytics.json"))
	if err != nil {
		return nil, err
	}
	versions := map[int64]bool{}
	for _, path := range paths {
		var config profileConfig
		if err := readJSON(path, &config); err != nil {
			return nil, err
		}
		if config.Version < 1 || versions[config.Version] || config.Requests < 1 || config.Requests > len(route) {
			return nil, fmt.Errorf("invalid/duplicate profile allocation: %s", path)
		}
		versions[config.Version] = true
		journal := filepath.Join(filepath.Dir(path), "profiles.jsonl")
		if _, err := os.Stat(journal); os.IsNotExist(err) {
			continue
		} else if err != nil {
			return nil, err
		}
		err = records(journal, func(e event) error {
			if e.Customer < 0 || e.Customer >= config.Requests || e.Customer%5 != 0 || e.First != config.Version || e.Count != 1 || e.Shard != route[e.Customer] {
				return fmt.Errorf("invalid profile witness: %s", journal)
			}
			if result[e.Customer] == nil {
				result[e.Customer] = map[int64]byte{}
			}
			flags := result[e.Customer][e.First]
			switch e.Stage {
			case "attempt":
				flags |= attempted
			case "ack":
				if flags&attempted == 0 || flags&acknowledged != 0 {
					return fmt.Errorf("profile ACK without attempt or duplicate")
				}
				flags |= acknowledged
			case "error":
			default:
				return fmt.Errorf("unknown profile event")
			}
			result[e.Customer][e.First] = flags
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}

func (p profileLedger) latestACK(customer int) int64 {
	var latest int64
	for version, flags := range p[customer] {
		if flags&acknowledged != 0 && version > latest {
			latest = version
		}
	}
	return latest
}

func verifyProfiles(ctx context.Context, db *sql.DB, route []int, owned map[int]bool, report *checkReport) error {
	ledger, err := profileHistory("/witness", route)
	if err != nil {
		return err
	}
	rows, err := db.QueryContext(ctx, query("PROFILE_QUERY", "SELECT customer_id,version,display_name FROM customer_profiles"))
	if err != nil {
		return err
	}
	defer rows.Close()
	seen := map[int]bool{}
	for rows.Next() {
		var customer int
		var version int64
		var name string
		actualShard := -1
		if report.Shard >= 0 {
			err = rows.Scan(&customer, &version, &name, &actualShard)
		} else {
			err = rows.Scan(&customer, &version, &name)
		}
		if err != nil {
			return err
		}
		report.Profiles++
		if customer < 0 || customer >= len(route) {
			report.Wrong++
			continue
		}
		if seen[customer] {
			report.Duplicates++
		}
		seen[customer] = true
		flags := ledger[customer][version]
		if flags&attempted == 0 || version < ledger.latestACK(customer) || name != profileName(version, customer) {
			report.Wrong++
		}
		if report.Shard >= 0 && (!owned[route[customer]] || actualShard != route[customer]) {
			report.WrongShard++
		}
		if flags&acknowledged == 0 {
			report.ProfileUnacked++
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for customer := range ledger {
		if report.Shard >= 0 && !owned[route[customer]] {
			continue
		}
		if ledger.latestACK(customer) > 0 && !seen[customer] {
			report.Missing++
		}
	}
	return nil
}
