// Independent SQL actors. A failed mutation is never automatically replayed.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

func payload(id int64) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("order:%d", id)))
	return strings.Repeat(fmt.Sprintf("customer-%05d|", id%10000), 13)[:192] + hex.EncodeToString(sum[:])
}

func number(name string, fallback, low, high int64) (int64, error) {
	s := os.Getenv(name)
	if s == "" {
		return fallback, nil
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil || n < low || n > high {
		return 0, fmt.Errorf("%s must be in %d..%d", name, low, high)
	}
	return n, nil
}

func connect(ctx context.Context) (*pgx.Conn, error) {
	cfg, err := pgx.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		return nil, err
	}
	cfg.ConnectTimeout = 2 * time.Second
	// Text protocol is shared by both systems; no driver-prepared statement cache.
	cfg.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol
	conn, err := pgx.ConnectConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	setting, expected := "synchronous_commit", "on"
	switch os.Getenv("BACKEND") {
	case "cedar":
		setting, expected = "async_commit", "off"
	case "postgres":
	default:
		closeConnection(conn)
		return nil, errors.New("BACKEND must be cedar or postgres")
	}
	if _, err = conn.Exec(ctx, "SET "+setting+"="+expected); err != nil {
		closeConnection(conn)
		return nil, err
	}
	var actual string
	if err = conn.QueryRow(ctx, "SHOW "+setting).Scan(&actual); err != nil || actual != expected {
		closeConnection(conn)
		return nil, fmt.Errorf("durability setting mismatch: %s=%s, error=%v", setting, actual, err)
	}
	if os.Getenv("BACKEND") == "postgres" {
		for _, setting := range []string{"fsync", "full_page_writes"} {
			if err = conn.QueryRow(ctx, "SHOW "+setting).Scan(&actual); err != nil || actual != "on" {
				closeConnection(conn)
				return nil, fmt.Errorf("required %s=on: value=%s error=%v", setting, actual, err)
			}
		}
	}
	if os.Getenv("READ_ONLY") == "1" {
		if _, err = conn.Exec(ctx, "BEGIN READ ONLY"); err != nil {
			closeConnection(conn)
			return nil, err
		}
	}
	return conn, nil
}

func seed(ctx context.Context, conn *pgx.Conn, count int64) error {
	if _, err := conn.Exec(ctx, "CREATE TABLE orders (id BIGINT PRIMARY KEY, customer INTEGER NOT NULL, amount INTEGER NOT NULL, payload TEXT NOT NULL)"); err != nil {
		return err
	}
	reader, writer := io.Pipe()
	generated := make(chan error, 1)
	go func() {
		csvOut := csv.NewWriter(writer)
		var err error
		for id := int64(0); id < count && err == nil; id++ {
			err = csvOut.Write([]string{strconv.FormatInt(id, 10), strconv.FormatInt(id%10000, 10), strconv.FormatInt((id%100000)*17%100000, 10), payload(id)})
		}
		csvOut.Flush()
		if err == nil {
			err = csvOut.Error()
		}
		writer.CloseWithError(err)
		generated <- err
	}()
	tag, err := conn.PgConn().CopyFrom(ctx, reader, "COPY orders (id,customer,amount,payload) FROM STDIN WITH (FORMAT CSV)")
	reader.CloseWithError(err)
	generatedErr := <-generated
	if err != nil {
		return err
	}
	if generatedErr != nil {
		return generatedErr
	}
	if tag.RowsAffected() != count {
		return fmt.Errorf("short COPY: %d != %d", tag.RowsAffected(), count)
	}
	return nil
}

type event struct {
	Kind      string `json:"kind"`
	ID        int64  `json:"id"`
	StartedNS int64  `json:"started_ns"`
	EndedNS   int64  `json:"ended_ns"`
	ElapsedNS int64  `json:"elapsed_ns"`
	Error     string `json:"error,omitempty"`
}

func record(file *os.File, value any) error {
	if err := json.NewEncoder(file).Encode(value); err != nil {
		return err
	}
	return file.Sync()
}

func createJournal(path string) (*os.File, error) {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return nil, err
	}
	dir, err := os.Open(filepath.Dir(path))
	if err == nil {
		err = dir.Sync()
		dir.Close()
	}
	if err != nil {
		file.Close()
		return nil, err
	}
	return file, nil
}

var errMismatch = errors.New("read contract violation")

func closeConnection(conn *pgx.Conn) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	conn.Close(ctx)
}

func readSample(ctx context.Context, conn *pgx.Conn, seq, seedCount int64) (int64, error) {
	oldID := (seq * 7919) % seedCount
	rows, err := conn.Query(ctx, "SELECT id,customer,amount,payload FROM orders WHERE id=$1 OR id=(SELECT max(id) FROM orders) ORDER BY id", oldID)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	seenOld, seen, newest := false, 0, int64(-1)
	for rows.Next() {
		var id, customer, amount int64
		var body string
		if err := rows.Scan(&id, &customer, &amount, &body); err != nil {
			return 0, err
		}
		if id <= newest || customer != id%10000 || amount != (id%100000)*17%100000 || body != payload(id) {
			return 0, fmt.Errorf("%w: wrong body/order for id %d", errMismatch, id)
		}
		seenOld = seenOld || id == oldID
		newest = id
		seen++
	}
	if err := rows.Err(); err != nil {
		return 0, err
	}
	if !seenOld || seen > 2 {
		return 0, fmt.Errorf("%w: missing old key or extra rows", errMismatch)
	}
	return newest, nil
}

func managedCommand(ctx context.Context, name string, args ...string) *exec.Cmd {
	command := exec.CommandContext(ctx, name, args...)
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Cancel = func() error {
		err := syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
		if err == syscall.ESRCH {
			return os.ErrProcessDone
		}
		return err
	}
	command.WaitDelay = 5 * time.Second
	return command
}

func actor(ctx context.Context, mode, path string, first, seedCount, rate int64) error {
	journal, err := createJournal(path)
	if err != nil {
		return err
	}
	defer journal.Close()
	var conn *pgx.Conn
	defer func() {
		if conn != nil {
			closeConnection(conn)
		}
	}()
	period := time.NewTicker(time.Second / time.Duration(rate))
	defer period.Stop()
	for seq := int64(0); ; seq++ {
		select {
		case <-ctx.Done():
			return nil
		case <-period.C:
		}
		if seq >= 1000000 {
			return errors.New("actor exhausted its bounded ID range")
		}
		id := first + seq
		started := time.Now()
		if mode == "writer" {
			if err := record(journal, event{Kind: "intent", ID: id, StartedNS: started.UnixNano()}); err != nil {
				return err
			}
		}
		requestStarted := time.Now()
		opCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		if conn == nil {
			conn, err = connect(opCtx)
		}
		submitted := false
		if err == nil {
			if mode == "writer" {
				submitted = true
				tag, writeErr := conn.Exec(opCtx, "INSERT INTO orders VALUES ($1,$2,$3,$4)", id, id%10000, (id%100000)*17%100000, payload(id))
				err = writeErr
				if err == nil && tag.RowsAffected() != 1 {
					err = fmt.Errorf("%w: short successful insert", errMismatch)
				}
			} else {
				id, err = readSample(opCtx, conn, seq, seedCount)
			}
		}
		cancel()
		finished := time.Now()
		value := event{Kind: "ok", ID: id, StartedNS: requestStarted.UnixNano(), EndedNS: finished.UnixNano(), ElapsedNS: finished.Sub(requestStarted).Nanoseconds()}
		if err != nil {
			value.Kind = "fail"
			if mode == "writer" && submitted {
				value.Kind = "ambiguous"
			}
			value.Error = err.Error()
			if errors.Is(err, errMismatch) {
				value.Kind = "mismatch"
				if recordErr := record(journal, value); recordErr != nil {
					return recordErr
				}
				return err
			}
			if conn != nil {
				closeConnection(conn)
				conn = nil
			}
		}
		if err := record(journal, value); err != nil {
			return err
		}
	}
}

func run() error {
	if len(os.Args) < 2 {
		return errors.New("usage: orders-workload seed|writer|reader|export|sql|pg-backup [path/query]")
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	count, err := number("ROWS", 1000000, 10000, 5000000)
	if err != nil {
		return err
	}
	mode := os.Args[1]
	if strings.HasPrefix(mode, "mutable-") {
		return runMutable(ctx, mode, count)
	}
	if mode == "maintenance" {
		if len(os.Args) != 3 {
			return errors.New("maintenance requires one trusted repository command")
		}
		bounded, cancel := context.WithTimeout(ctx, 240*time.Second)
		defer cancel()
		command := managedCommand(bounded, "/bin/bash", "-euc", os.Args[2])
		var output strings.Builder
		command.Stdout, command.Stderr = &output, os.Stderr
		err := command.Run()
		if encodeErr := json.NewEncoder(os.Stdout).Encode(map[string]string{"kind": "maintenance-output", "stdout": output.String()}); encodeErr != nil {
			return encodeErr
		}
		return err
	}
	if mode == "pg-backup" {
		if len(os.Args) != 3 || !strings.HasPrefix(os.Args[2], "/backups/postgres/") || filepath.Clean(os.Args[2]) != os.Args[2] {
			return errors.New("pg-backup requires a new directory beneath /backups/postgres")
		}
		if _, err := os.Lstat(os.Args[2]); !errors.Is(err, os.ErrNotExist) {
			return errors.New("backup destination exists or cannot be inspected")
		}
		bounded, cancel := context.WithTimeout(ctx, 240*time.Second)
		defer cancel()
		command := managedCommand(bounded, "pg_basebackup", "-D", os.Args[2], "-h", "postgres", "-U", "backup_agent", "-X", "stream", "--checkpoint=fast", "--no-password")
		command.Stdout, command.Stderr = os.Stdout, os.Stderr
		return command.Run()
	}
	if mode == "writer" || mode == "reader" {
		if len(os.Args) != 3 {
			return errors.New("actor requires an exclusive journal path")
		}
		first, err := number("FIRST_ID", 1000000000, 1000000000, 1000000000000)
		if err != nil {
			return err
		}
		rate, err := number("RATE", 50, 1, 1000)
		if err != nil {
			return err
		}
		return actor(ctx, mode, os.Args[2], first, count, rate)
	}
	conn, err := connect(ctx)
	if err != nil {
		return err
	}
	defer closeConnection(conn)
	switch mode {
	case "analytics":
		return analytics(ctx, conn, count)
	case "seed":
		return seed(ctx, conn, count)
	case "export":
		if len(os.Args) != 3 {
			return errors.New("export requires a new file path")
		}
		file, err := createJournal(os.Args[2])
		if err != nil {
			return err
		}
		defer file.Close()
		_, err = conn.PgConn().CopyTo(ctx, file, "COPY (SELECT id,customer,amount,payload FROM orders ORDER BY id) TO STDOUT WITH (FORMAT CSV, HEADER TRUE)")
		if err == nil {
			err = file.Sync()
		}
		return err
	case "sql":
		if len(os.Args) != 3 {
			return errors.New("sql requires one trusted lab query")
		}
		rows, err := conn.Query(ctx, os.Args[2])
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			values, err := rows.Values()
			if err != nil {
				return err
			}
			if err := json.NewEncoder(os.Stdout).Encode(values); err != nil {
				return err
			}
		}
		return rows.Err()
	default:
		return errors.New("unknown action")
	}
}

func cpuSeconds(usage syscall.Rusage) float64 {
	return float64(usage.Utime.Sec+usage.Stime.Sec) + float64(usage.Utime.Usec+usage.Stime.Usec)/1e6
}

func main() {
	var initial, initialChildren syscall.Rusage
	initialError := syscall.Getrusage(syscall.RUSAGE_SELF, &initial)
	initialChildrenError := syscall.Getrusage(syscall.RUSAGE_CHILDREN, &initialChildren)
	started := time.Now()
	err := run()
	finished := time.Now()
	var usage syscall.Rusage
	counters := map[string]any{"kind": "process-resources", "scope": "Linux Docker process lifetime; CPU covers all Go threads. Native cgroup peaks include charged cache/kernel memory."}
	if rusageErr := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); rusageErr != nil {
		counters["rusage_error"] = rusageErr.Error()
	} else {
		counters["user_cpu_seconds"] = float64(usage.Utime.Sec) + float64(usage.Utime.Usec)/1e6
		counters["system_cpu_seconds"] = float64(usage.Stime.Sec) + float64(usage.Stime.Usec)/1e6
		counters["peak_rss_kib"] = usage.Maxrss
	}
	var children syscall.Rusage
	if childErr := syscall.Getrusage(syscall.RUSAGE_CHILDREN, &children); childErr != nil {
		counters["children_rusage_error"] = childErr.Error()
	} else {
		counters["children_user_cpu_seconds"] = float64(children.Utime.Sec) + float64(children.Utime.Usec)/1e6
		counters["children_system_cpu_seconds"] = float64(children.Stime.Sec) + float64(children.Stime.Usec)/1e6
		counters["children_peak_rss_kib"] = children.Maxrss
	}
	for _, name := range []string{"memory.peak", "cpu.stat", "io.stat", "memory.events", "cpu.max", "memory.max", "memory.swap.max"} {
		raw, readErr := os.ReadFile("/sys/fs/cgroup/" + name)
		if readErr != nil {
			counters[name] = map[string]string{"error": readErr.Error()}
		} else {
			counters[name] = string(raw)
		}
	}
	elapsed := finished.Sub(started).Seconds()
	counters["main_wall_seconds"] = elapsed
	if elapsed > 0 && initialError == nil && initialChildrenError == nil && counters["rusage_error"] == nil && counters["children_rusage_error"] == nil {
		delta := cpuSeconds(usage) - cpuSeconds(initial) + cpuSeconds(children) - cpuSeconds(initialChildren)
		counters["main_cpu_seconds_self_and_reaped_children"] = delta
		counters["cpu_percent_one_core_100_self_and_reaped_children"] = delta / elapsed * 100
	} else {
		counters["main_cpu_utilization_unavailable"] = "A matching initial/final rusage snapshot or positive wall interval is unavailable"
	}
	counters["timing_scope"] = "Main wall time and CPU deltas bracket the operation and connection close; process-lifetime totals separately include runtime startup. Rusage/timer calls add small measurement overhead; native counters are sampled before final JSON/exit. Cgroup counters include parent/children; do not add them to process CPU again."
	json.NewEncoder(os.Stdout).Encode(counters)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
