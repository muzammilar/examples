package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type profileConfig struct {
	Version    int64 `json:"version"`
	Operations int   `json:"operations"`
}
type profileMutation struct {
	event event
	flags byte
}
type profileLedger map[int]map[int64]profileMutation

func profileHistory(root string, total int) (profileLedger, error) {
	paths, err := filepath.Glob(filepath.Join(root, "profiles", "*", "run.json"))
	if err != nil {
		return nil, err
	}
	type allocation struct {
		path   string
		config profileConfig
	}
	var allocations []allocation
	for _, path := range paths {
		var c profileConfig
		if err = readJSON(path, &c); err != nil {
			return nil, err
		}
		if c.Version < 1 || c.Operations < 1 || c.Operations > total {
			return nil, errors.New("invalid profile allocation")
		}
		allocations = append(allocations, allocation{path, c})
	}
	sort.Slice(allocations, func(i, j int) bool { return allocations[i].config.Version < allocations[j].config.Version })
	ledger := profileLedger{}
	for i, a := range allocations {
		c := a.config
		if i > 0 && allocations[i-1].config.Version == c.Version {
			return nil, errors.New("duplicate profile version allocation")
		}
		journal := filepath.Join(filepath.Dir(a.path), "profiles.jsonl")
		if _, err = os.Stat(journal); os.IsNotExist(err) {
			continue
		} else if err != nil {
			return nil, err
		}
		err = records(journal, func(e event) error {
			if e.Customer < 0 || e.Customer >= c.Operations || e.Customer%5 != 0 || e.First != c.Version || e.Count != 1 || e.Quota < 0 || e.Quota != e.PreviousQuota-1 || e.PreviousVersion >= e.First {
				return errors.New("invalid profile mutation witness")
			}
			previous := ledger[e.Customer][e.PreviousVersion]
			if e.PreviousVersion == 0 {
				if e.PreviousQuota != 1000 {
					return errors.New("invalid initial quota")
				}
			} else if previous.flags&attempted == 0 || previous.event.Quota != e.PreviousQuota {
				return errors.New("profile predecessor lacks witness")
			}
			if ledger[e.Customer] == nil {
				ledger[e.Customer] = map[int64]profileMutation{}
			}
			v := ledger[e.Customer][e.First]
			if v.flags != 0 && (v.event.Quota != e.Quota || v.event.PreviousQuota != e.PreviousQuota || v.event.PreviousVersion != e.PreviousVersion) {
				return errors.New("profile retry changed its intended body")
			}
			switch e.Stage {
			case "attempt":
				v.flags |= attempted
			case "ack":
				if v.flags&attempted == 0 || v.flags&acknowledged != 0 {
					return errors.New("profile ACK without attempt or duplicate")
				}
				v.flags |= acknowledged
			case "error":
				if v.flags&attempted == 0 {
					return errors.New("profile error without attempt")
				}
			default:
				return errors.New("unknown profile event")
			}
			v.event = e
			ledger[e.Customer][e.First] = v
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return ledger, nil
}
func (p profileLedger) latestACK(id int) (int64, int64) {
	version, quota := int64(0), int64(1000)
	for v, m := range p[id] {
		if m.flags&acknowledged != 0 && v > version {
			version = v
			quota = m.event.Quota
		}
	}
	return version, quota
}

// Initialization is resumable without upserts: verify every existing body, then
// insert only missing keys. NDB can batch plain INSERTs; duplicate-key upserts
// force per-row peeks. The controller serializes setup before live workloads.
func seedCustomers(db *sql.DB, f fixture) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	rows, err := db.QueryContext(ctx, "SELECT customer_id,name FROM customers")
	if err != nil {
		return err
	}
	seen := make([]bool, f.Customers)
	for rows.Next() {
		var id int
		var name string
		if err = rows.Scan(&id, &name); err != nil {
			rows.Close()
			return err
		}
		if id < 0 || id >= f.Customers || seen[id] || name != fmt.Sprintf("customer-%d", id) {
			rows.Close()
			return errors.New("existing customer seed body is not canonical")
		}
		seen[id] = true
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for first := 0; first < f.Customers; first += 500 {
		var text strings.Builder
		text.WriteString("INSERT INTO customers(customer_id,name) VALUES")
		args := []any{}
		for id := first; id < min(first+500, f.Customers); id++ {
			if seen[id] {
				continue
			}
			if len(args) > 0 {
				text.WriteByte(',')
			}
			text.WriteString("(?,?)")
			args = append(args, id, fmt.Sprintf("customer-%d", id))
		}
		if len(args) > 0 {
			if _, err = db.ExecContext(ctx, text.String(), args...); err != nil {
				return err
			}
		}
	}
	return nil
}

func seedSubscribers(db *sql.DB, f fixture) error {
	if f.Subscribers < 1 || f.Subscribers > 10000000 {
		return errors.New("invalid subscriber fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	started := time.Now()
	rows, err := db.QueryContext(ctx, "SELECT subscriber_id,phone,lookup_ref,quota,version FROM subscribers")
	if err != nil {
		return err
	}
	seen := make([]bool, f.Subscribers)
	existing := 0
	for rows.Next() {
		var id int
		var phone, ref, quota, version int64
		if err = rows.Scan(&id, &phone, &ref, &quota, &version); err != nil {
			rows.Close()
			return err
		}
		if id < 0 || id >= f.Subscribers || seen[id] || phone != 15550000000+int64(id) || ref != phone || quota != 1000 || version != 0 {
			rows.Close()
			return errors.New("existing subscriber seed body is not canonical")
		}
		seen[id] = true
		existing++
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for first := 0; first < f.Subscribers; first += 500 {
		var text strings.Builder
		text.WriteString("INSERT INTO subscribers(subscriber_id,phone,lookup_ref,quota,version) VALUES")
		args := []any{}
		for id := first; id < min(first+500, f.Subscribers); id++ {
			if seen[id] {
				continue
			}
			if len(args) > 0 {
				text.WriteByte(',')
			}
			text.WriteString("(?,?,?,1000,0)")
			args = append(args, id, 15550000000+int64(id), 15550000000+int64(id))
		}
		if len(args) > 0 {
			if _, err = db.ExecContext(ctx, text.String(), args...); err != nil {
				return err
			}
		}
	}
	return save("/results/seed-profiles.json", map[string]any{"subscribers": f.Subscribers, "existing_verified": existing, "new_rows": f.Subscribers - existing, "seconds": time.Since(started).Seconds()})
}

// NDB SQL frontends share storage. This scans full logical bodies, not each
// physical fragment replica. Native replica placement is checked separately.
func verifySubscribers(ctx context.Context, conn *sql.Conn, f fixture, report *checkReport) error {
	ledger, err := profileHistory("/witness", f.Subscribers)
	if err != nil {
		return err
	}
	rows, err := conn.QueryContext(ctx, "SELECT subscriber_id,phone,lookup_ref,quota,version FROM subscribers")
	if err != nil {
		return err
	}
	defer rows.Close()
	seen := make([]bool, f.Subscribers)
	for rows.Next() {
		var id int
		var phone, ref, quota, version int64
		if err = rows.Scan(&id, &phone, &ref, &quota, &version); err != nil {
			return err
		}
		report.Profiles++
		if id < 0 || id >= f.Subscribers {
			report.Wrong++
			continue
		}
		if seen[id] {
			report.Duplicates++
		}
		seen[id] = true
		latest, _ := ledger.latestACK(id)
		if phone != 15550000000+int64(id) || ref != phone || version < latest {
			report.Wrong++
		}
		if version == 0 {
			if quota != 1000 {
				report.Wrong++
			}
			continue
		}
		m := ledger[id][version]
		if m.flags&attempted == 0 || m.event.Quota != quota {
			report.Wrong++
		}
		if m.flags&acknowledged == 0 {
			report.ProfileUnacked++
		}
	}
	if err = rows.Err(); err != nil {
		return err
	}
	for _, exists := range seen {
		if !exists {
			report.Missing++
		}
	}
	return nil
}
