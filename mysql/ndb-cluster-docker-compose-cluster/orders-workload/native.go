package main

import (
	"bufio"
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
)

// The pinned official ndb_select_all --rowid tool exposes the FRAGMENT pseudo
// column. It scans a logical copy of each fragment, not every physical replica.
func scanNative(input io.Reader, header string, visit func(int, []string) error) (int, error) {
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 4096), 64*1024)
	if !scanner.Scan() || scanner.Text() != header {
		return 0, errors.New("native scan header differs")
	}
	columns := len(strings.Split(header, "\t"))
	seen := 0
	completed := false
	for scanner.Scan() {
		line := scanner.Text()
		if completed {
			return seen, errors.New("data after native scan completion")
		}
		if !strings.HasPrefix(line, "[") {
			footer := strings.Fields(line)
			if len(footer) != 3 || footer[1] != "rows" || footer[2] != "returned" {
				return seen, fmt.Errorf("invalid native scan footer: %q", line)
			}
			count, err := strconv.Atoi(footer[0])
			if err != nil || count != seen {
				return seen, errors.New("native scan completion count differs")
			}
			completed = true
			continue
		}
		fields := strings.Split(line, "\t")
		if len(fields) != columns {
			return seen, errors.New("native scan column count differs")
		}
		id := strings.Fields(fields[0])
		if len(id) != 8 || id[0] != "[" || id[1] != "fragment:" || id[3] != "m_page:" || id[5] != "m_page_idx:" || id[7] != "]" {
			return seen, errors.New("native ROWID layout differs")
		}
		fragment, err := strconv.Atoi(id[2])
		if err != nil || fragment < 0 || fragment > 2 {
			return seen, errors.New("native fragment outside expected range")
		}
		for _, value := range []string{id[4], id[6]} {
			if _, err = strconv.ParseUint(value, 10, 64); err != nil {
				return seen, err
			}
		}
		if err = visit(fragment, fields[1:]); err != nil {
			return seen, err
		}
		seen++
	}
	if err := scanner.Err(); err != nil {
		return seen, err
	}
	if !completed {
		return seen, errors.New("native scan ended without completion footer")
	}
	return seen, nil
}

func verifyNative(f fixture, route []int, mode string) error {
	file, err := os.Open("/results/native-scan.gz")
	if err != nil {
		return err
	}
	defer file.Close()
	input, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	defer input.Close()
	report := checkReport{Shard: -1}
	if mode == "native-orders" {
		ledger, err := history("/witness", route)
		if err != nil {
			return err
		}
		report.Histories = len(ledger.runs)
		_, err = scanNative(input, "ROWID\tcustomer_id\top_id\tamount", func(fragment int, fields []string) error {
			customer, e := strconv.Atoi(fields[0])
			if e != nil {
				return e
			}
			id, e := strconv.ParseInt(fields[1], 10, 64)
			if e != nil {
				return e
			}
			amount, e := strconv.ParseInt(fields[2], 10, 64)
			if e != nil {
				return e
			}
			report.Shard = fragment
			ledger.observe(&report, id, customer, amount)
			return nil
		})
		if err != nil {
			return err
		}
		report.Shard = -1
		err = ledger.finish(&report)
		if saveErr := save("/results/check.json", report); saveErr != nil {
			return saveErr
		}
		return err
	}
	seen := make([]bool, f.Customers)
	_, err = scanNative(input, "ROWID\tcustomer_id\tname", func(fragment int, fields []string) error {
		id, e := strconv.Atoi(fields[0])
		if e != nil {
			return e
		}
		report.Customers++
		if id < 0 || id >= f.Customers {
			report.Wrong++
			return nil
		}
		if seen[id] {
			report.Duplicates++
		}
		seen[id] = true
		if route[id] != fragment {
			report.WrongShard++
		}
		// Names are checked with MySQL's decoded string representation in the full
		// SQL oracle. This native scan independently checks ID/fragment placement.
		return nil
	})
	if err != nil {
		return err
	}
	for _, exists := range seen {
		if !exists {
			report.Missing++
		}
	}
	if err = save("/results/check.json", report); err != nil {
		return err
	}
	if report.Wrong+report.Missing+report.Duplicates+report.WrongShard > 0 {
		return errors.New("native customer placement reconciliation failed")
	}
	return nil
}
