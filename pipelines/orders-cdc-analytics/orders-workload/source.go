package main

import (
	"compress/gzip"
	"context"
	"database/sql"
	"fmt"
	"io"
	"os"
	"time"
)

func mutation(ctx context.Context, pg *sql.DB, j *journal, id, operation string, first, last int, query string) error {
	intent := map[string]any{"event": "intent", "operation_id": id, "operation": operation, "first_id": first, "last_id": last}
	if err := j.append(intent); err != nil {
		return err
	}
	started := time.Now()
	err := func() error {
		op, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		tx, err := pg.BeginTx(op, nil)
		if err != nil {
			return err
		}
		defer tx.Rollback()
		result, err := tx.ExecContext(op, query)
		if err != nil {
			return err
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if affected != int64(last-first+1) {
			return fmt.Errorf("mutation affected %d rows, expected %d", affected, last-first+1)
		}
		if _, err = tx.ExecContext(op, "INSERT INTO receipts(id,operation,first_id,last_id,affected) VALUES ($1,$2,$3,$4,$5)", id, operation, first, last, affected); err != nil {
			return err
		}
		if err = tx.Commit(); err != nil {
			return err
		}
		intent["affected"] = affected
		return nil
	}()
	intent["duration_ms"] = float64(time.Since(started).Nanoseconds()) / 1e6
	if err != nil {
		intent["event"] = "ambiguous"
		intent["error"] = err.Error()
	} else {
		intent["event"] = "ack"
	}
	if persist := j.append(intent); persist != nil {
		return persist
	}
	emit(intent)
	return err
}
func source(ctx context.Context, pg *sql.DB, mode, run string, n, customers int) error {
	j, err := openJournal(run, mode)
	if err != nil {
		return err
	}
	defer j.file.Close()
	apply := func(seq int, op string, first, last int, q string) error {
		return mutation(ctx, pg, j, fmt.Sprintf("%s-%d", j.prefix, seq), op, first, last, q)
	}
	switch mode {
	case "seed":
		var existing int
		if err = pg.QueryRowContext(ctx, "SELECT count(*) FROM orders").Scan(&existing); err != nil {
			return err
		}
		if existing != 0 {
			return fmt.Errorf("refuse to reseed retained rows")
		}
		if err = apply(0, "customers", 1, customers, fmt.Sprintf("INSERT INTO customers SELECT i, 'region-' || (i %% 16)::text, 1 FROM generate_series(1,%d) i", customers)); err != nil {
			return err
		}
		for first := 1; first <= n; first += 25000 {
			last := min(first+24999, n)
			q := fmt.Sprintf("INSERT INTO orders SELECT i, 1+(i-1)%%%d,100+i%%1000,1,false,1700000000+i%%86400 FROM generate_series(%d,%d) i", customers, first, last)
			if err = apply(first, "seed-orders", first, last, q); err != nil {
				return err
			}
		}
		return reference(run, n, customers)
	case "mutate":
		steps, err := count("MUTATION_STEPS", 200)
		if err != nil || steps < 1 || steps > 2000 {
			return fmt.Errorf("MUTATION_STEPS must be between 1 and 2000")
		}
		for step := 0; step < steps; step++ {
			if _, err = os.Stat("/results/stop-writer-" + actorToken(run)); err == nil {
				break
			} else if !os.IsNotExist(err) {
				return err
			}
			first := (step%(min(n/10, 10000)/100))*100 + 1
			last := first + 99
			q := fmt.Sprintf("UPDATE orders SET revision=revision+1,amount_cents=100+id%%1000+(revision+1)*10,cancelled=(id%%7=0 AND (revision+1)%%2=0),event_second=1690000000+id%%86400 WHERE id BETWEEN %d AND %d", first, last)
			if err = apply(step, "correct-orders", first, last, q); err != nil {
				return err
			}
			time.Sleep(100 * time.Millisecond)
		}
	case "dimensions":
		limit := min(100, customers/4)
		return apply(0, "dimension-correction", 1, limit, fmt.Sprintf("UPDATE customers SET region='corrected-' || (id%%16)::text,revision=revision+1 WHERE id<=%d", limit))
	case "delete":
		if err = apply(0, "hard-delete", 1, 1000, "DELETE FROM orders WHERE id<=1000"); err != nil {
			return err
		}
		return apply(1, "cancel-orders", 1001, 2000, "UPDATE orders SET cancelled=true,revision=revision+1 WHERE id BETWEEN 1001 AND 2000")
	default:
		return fmt.Errorf("unknown source mode %q", mode)
	}
	return nil
}

type byteCounter struct {
	writer io.Writer
	bytes  int64
}

func (c *byteCounter) Write(p []byte) (int, error) {
	n, err := c.writer.Write(p)
	c.bytes += int64(n)
	return n, err
}
func reference(run string, n, customers int) error {
	file, err := os.OpenFile("/results/source-reference-"+run+".csv.gz", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	gz, err := gzip.NewWriterLevel(file, gzip.BestSpeed)
	if err != nil {
		return err
	}
	counter := &byteCounter{writer: gz}
	started := time.Now()
	for i := 1; i <= n; i++ {
		if _, err = fmt.Fprintf(counter, "%d,%d,%d,1,false,%d\n", i, 1+(i-1)%customers, 100+i%1000, 1700000000+i%86400); err != nil {
			return err
		}
	}
	if err = gz.Close(); err != nil {
		return err
	}
	if err = file.Sync(); err != nil {
		return err
	}
	info, err := file.Stat()
	if err != nil {
		return err
	}
	emit(map[string]any{"event": "reference_compression", "codec": "gzip-level-1", "scope": "generated source CSV reference, not native database compression", "uncompressed_bytes": counter.bytes, "compressed_bytes": info.Size(), "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6})
	return nil
}
