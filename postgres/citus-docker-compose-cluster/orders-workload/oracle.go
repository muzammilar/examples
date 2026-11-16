package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

const attempted = 1
const acknowledged = 2
const observed = 4

type ledgerRun struct {
	config runConfig
	flags  []byte
	path   string
}
type ledger struct {
	runs  []ledgerRun
	route []int
	owned map[int]bool
}

func history(root string, route []int) (*ledger, error) {
	paths, err := filepath.Glob(filepath.Join(root, "runs", "*", "run.json"))
	if err != nil {
		return nil, err
	}
	l := &ledger{route: route}
	for _, path := range paths {
		var c runConfig
		if err := readJSON(path, &c); err != nil {
			return nil, err
		}
		if c.Base < 0 || c.Reserved < 1 || c.Reserved > 100000000 || c.Batch < 1 || c.Customers != len(route) {
			return nil, errors.New("invalid historical allocation")
		}
		r := ledgerRun{config: c, flags: make([]byte, c.Reserved), path: filepath.Dir(path)}
		writes := filepath.Join(r.path, "writes.jsonl")
		// A reserved run can fail before its writer creates a journal. It has no
		// attempted keys; any data from that allocation will still fail observation.
		matches, err := filepath.Glob(writes)
		if err != nil {
			return nil, err
		}
		if len(matches) > 0 {
			if err = records(writes, func(e event) error { return r.record(e, route) }); err != nil {
				return nil, err
			}
		}
		l.runs = append(l.runs, r)
	}
	sort.Slice(l.runs, func(i, j int) bool { return l.runs[i].config.Base < l.runs[j].config.Base })
	for i := 1; i < len(l.runs); i++ {
		p, c := l.runs[i-1].config, l.runs[i].config
		if p.Base+int64(p.Reserved) > c.Base {
			return nil, errors.New("overlapping historical ID allocations")
		}
	}
	return l, nil
}
func (r *ledgerRun) record(e event, route []int) error {
	c := r.config
	offset := e.First - c.Base
	if offset < 0 || offset >= int64(c.Reserved) || offset%int64(c.Batch) != 0 || e.Count != min(c.Batch, c.Reserved-int(offset)) || e.Customer != (int(offset)/c.Batch)%c.Customers || e.Shard != route[e.Customer] {
		return errors.New("witness does not match allocated deterministic batch")
	}
	if e.Stage != "attempt" && e.Stage != "ack" && e.Stage != "error" {
		return errors.New("unknown witness event")
	}
	for i := int(offset); i < int(offset)+e.Count; i++ {
		if e.Stage == "attempt" {
			r.flags[i] |= attempted
		}
		if e.Stage == "ack" {
			if r.flags[i]&attempted == 0 || r.flags[i]&acknowledged != 0 {
				return errors.New("ACK without attempt or duplicate ACK")
			}
			r.flags[i] |= acknowledged
		}
	}
	return nil
}
func (l *ledger) runFor(id int64) (*ledgerRun, int) {
	i := sort.Search(len(l.runs), func(i int) bool { return l.runs[i].config.Base > id }) - 1
	if i < 0 {
		return nil, 0
	}
	r := &l.runs[i]
	offset := id - r.config.Base
	if offset < 0 || offset >= int64(r.config.Reserved) {
		return nil, 0
	}
	return r, int(offset)
}

type checkReport struct {
	Histories      int `json:"histories"`
	Shard          int `json:"expected_group"`
	Customers      int `json:"customers"`
	Rows           int `json:"rows"`
	Acked          int `json:"acknowledged_rows"`
	Missing        int `json:"missing_acknowledged"`
	Wrong          int `json:"wrong_values"`
	WrongShard     int `json:"wrong_shard"`
	Unexpected     int `json:"unexpected_rows"`
	Duplicates     int `json:"duplicate_ids"`
	Partial        int `json:"partial_transactions"`
	Unacked        int `json:"committed_without_ack"`
	Profiles       int `json:"profiles"`
	ProfileUnacked int `json:"profiles_without_ack"`
}

func (l *ledger) observe(report *checkReport, id int64, customer int, amount int64) {
	report.Rows++
	r, offset := l.runFor(id)
	if r == nil || r.flags[offset]&attempted == 0 {
		report.Unexpected++
		return
	}
	c := r.config
	expectedCustomer := (offset / c.Batch) % c.Customers
	if customer != expectedCustomer || amount != id%10000+1 {
		report.Wrong++
	}
	if report.Shard >= 0 && !l.owned[l.route[expectedCustomer]] {
		report.WrongShard++
	}
	if r.flags[offset]&observed != 0 {
		report.Duplicates++
	}
	r.flags[offset] |= observed
	if r.flags[offset]&acknowledged == 0 {
		report.Unacked++
	}
}
func (l *ledger) finish(report *checkReport) error {
	for _, r := range l.runs {
		c := r.config
		for first := 0; first < c.Reserved; first += c.Batch {
			customer := (first / c.Batch) % c.Customers
			if report.Shard >= 0 && !l.owned[l.route[customer]] {
				continue
			}
			n := min(c.Batch, c.Reserved-first)
			seen := 0
			for i := first; i < first+n; i++ {
				v := r.flags[i]
				if v&acknowledged != 0 {
					report.Acked++
					if v&observed == 0 {
						report.Missing++
					}
				}
				if v&observed != 0 {
					seen++
				}
			}
			if seen > 0 && seen < n {
				report.Partial++
			}
		}
	}
	if report.Missing+report.Wrong+report.WrongShard+report.Unexpected+report.Duplicates+report.Partial > 0 {
		return errors.New("full historical body/placement reconciliation failed")
	}
	return nil
}
func verify(db *sql.DB, f fixture, route []int, shard int) error {
	l, err := history("/witness", route)
	if err != nil {
		return err
	}
	report := checkReport{Histories: len(l.runs), Shard: shard}
	if shard >= 0 {
		var owned []int
		if err := json.Unmarshal([]byte(os.Getenv("OWNED_SHARDS")), &owned); err != nil {
			return err
		}
		l.owned = map[int]bool{}
		for _, id := range owned {
			l.owned[id] = true
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	rows, err := db.QueryContext(ctx, query("CUSTOMER_QUERY", "SELECT customer_id,name FROM customers"))
	if err != nil {
		return err
	}
	customers := make([]bool, f.Customers)
	for rows.Next() {
		var id int
		var name string
		actualShard := -1
		if shard >= 0 {
			err = rows.Scan(&id, &name, &actualShard)
		} else {
			err = rows.Scan(&id, &name)
		}
		if err != nil {
			rows.Close()
			return err
		}
		if id < 0 || id >= f.Customers || customers[id] || name != fmt.Sprintf("customer-%d", id) || (shard >= 0 && (!l.owned[route[id]] || actualShard != route[id])) {
			report.Wrong++
		} else {
			customers[id] = true
		}
		report.Customers++
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for c, seen := range customers {
		if (shard < 0 || l.owned[route[c]]) && !seen {
			report.Wrong++
		}
	}
	rows, err = db.QueryContext(ctx, query("ORDER_QUERY", "SELECT op_id,customer_id,amount FROM orders"))
	if err != nil {
		return err
	}
	for rows.Next() {
		var id, amount int64
		var customer int
		actualShard := -1
		if shard >= 0 {
			err = rows.Scan(&id, &customer, &amount, &actualShard)
		} else {
			err = rows.Scan(&id, &customer, &amount)
		}
		if err != nil {
			rows.Close()
			return err
		}
		if shard >= 0 {
			l.observePhysical(&report, id, customer, amount, actualShard)
		} else {
			l.observe(&report, id, customer, amount)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if err = verifyProfiles(ctx, db, route, l.owned, &report); err != nil {
		return err
	}
	reconcileErr := l.finish(&report)
	if err = save("/results/check.json", report); err != nil {
		return err
	}
	if reconcileErr != nil {
		return reconcileErr
	}
	if report.Wrong != 0 {
		return errors.New("customer oracle failed")
	}
	return nil
}

func query(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func (l *ledger) observePhysical(report *checkReport, id int64, customer int, amount int64, actualShard int) {
	l.observe(report, id, customer, amount)
	if customer < 0 || customer >= len(l.route) || actualShard != l.route[customer] {
		report.WrongShard++
	}
}
