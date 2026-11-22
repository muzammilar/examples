package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestRecoveryInspectionNativeSnapshotAndPhysicalFile(t *testing.T) {
	c := recoveryTestConfig()
	c.Rows, c.Customers, c.Batch = 25, 5, 10
	attempts := []recoveryAttempt{
		recoveryTestAttempt(t, c, -1, 0, "ok"), recoveryTestAttempt(t, c, -1, 1, "ok"), recoveryTestAttempt(t, c, -1, 2, "ok"),
		// An ACK made after the selected snapshot is not required in that pin.
		recoveryTestAttempt(t, c, 0, 0, "ok"),
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	_, err = db.Exec("CREATE TABLE fixture AS SELECT i::BIGINT event_id,(i%5)::INTEGER customer_id,(i%10000+1)::BIGINT amount_cents,(-1)::INTEGER writer_id,((i//10)*10)::BIGINT batch_start,least((i//10)*10+10,25)::BIGINT batch_end,sha256(CAST(i AS VARCHAR)) payload_sha256 FROM range(25) t(i)")
	if err != nil {
		t.Fatal(err)
	}
	read := func(query string, complete bool) (recoveryInspectionProof, error) {
		t.Helper()
		rows, e := db.Query(query)
		if e != nil {
			return recoveryInspectionProof{}, e
		}
		p, e := recoveryInspectRows(rows, c, attempts, complete)
		closeErr := rows.Close()
		if e == nil {
			e = closeErr
		}
		return p, e
	}
	proof, err := read("SELECT * FROM fixture ORDER BY event_id", true)
	if err != nil {
		t.Fatal(err)
	}
	// Independently generated canonical CSV fixture, not the production oracle.
	if proof.Rows != 25 || proof.SeedRows != 25 || proof.Batches != 3 || proof.BodyHash != "e7ce715f8a32ab53722284d925052100ecb4e3b359c717bc4be2cfa197ef2ae2" {
		t.Fatalf("wrong complete proof: %+v", proof)
	}
	for name, query := range map[string]string{
		"partial-seed": "SELECT * FROM fixture WHERE event_id!=0 ORDER BY event_id",
		"duplicate":    "SELECT * FROM fixture UNION ALL SELECT * FROM fixture WHERE event_id=24 ORDER BY event_id",
		"wrong-body":   "SELECT event_id,customer_id,amount_cents+1,writer_id,batch_start,batch_end,payload_sha256 FROM fixture ORDER BY event_id",
		"wrong-hash":   "SELECT event_id,customer_id,amount_cents,writer_id,batch_start,batch_end,'wrong' FROM fixture ORDER BY event_id",
		"null-body":    "SELECT event_id,NULL::INTEGER,amount_cents,writer_id,batch_start,batch_end,payload_sha256 FROM fixture ORDER BY event_id",
		"empty":        "SELECT * FROM fixture WHERE false",
	} {
		t.Run(name, func(t *testing.T) {
			if _, e := read(query, true); e == nil {
				t.Fatal("invalid snapshot accepted")
			}
		})
	}
	if _, err = read("SELECT * FROM fixture WHERE event_id<5 ORDER BY event_id", true); err == nil {
		t.Fatal("partial committed batch accepted")
	}
	partial, err := read("SELECT * FROM fixture WHERE event_id<5 ORDER BY event_id", false)
	if err != nil || partial.Rows != 5 {
		t.Fatalf("physical file may split a batch: %+v %v", partial, err)
	}
	path := filepath.Join(t.TempDir(), "candidate.parquet")
	if _, err = db.Exec("COPY (SELECT * FROM fixture WHERE event_id<5 ORDER BY event_id) TO " + quote(path) + " (FORMAT PARQUET)"); err != nil {
		t.Fatal(err)
	}
	file, err := recoveryParquetMetadata(context.Background(), db, path, 5)
	if err != nil || file.Bytes <= file.Footer || file.Rows != 5 {
		t.Fatalf("native footer: %+v %v", file, err)
	}
	body, err := read("SELECT * FROM read_parquet("+quote(path)+") ORDER BY event_id", false)
	if err != nil || body != partial {
		t.Fatalf("native Parquet body: %+v %v", body, err)
	}
}

func TestRecoveryInspectionCatalogUsesExactSnapshotIDs(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "catalog.json")
	write := func(ids string) {
		t.Helper()
		if err := os.WriteFile(file, []byte(`{"available":true,"load_table":{"metadata":{"snapshots":[`+ids+`]}}}`), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"snapshot-id":9007199254740993,"summary":{"operation":"append"}},{"snapshot-id":9223372036854775806,"summary":{"operation":"replace"}}`)
	ops, err := recoveryInspectionOperations(dir)
	if err != nil || len(ops) != 2 || ops[0].ID != 9007199254740993 || ops[1].ID != 9223372036854775806 {
		t.Fatalf("exact history: %+v %v", ops, err)
	}
	for _, value := range []string{`{"snapshot-id":1,"summary":{}}`, `{"snapshot-id":-1,"summary":{"operation":"append"}}`, `{"snapshot-id":1,"summary":{"operation":"append"}},{"snapshot-id":1,"summary":{"operation":"append"}}`} {
		write(value)
		if _, err = recoveryInspectionOperations(dir); err == nil {
			t.Fatal("invalid snapshot history accepted")
		}
	}
}

func TestRecoveryInspectionPrefixBelongsToNativeTable(t *testing.T) {
	c := recoveryTestConfig()
	uuid := "5742e029-c49e-4e57-8832-ddc6495e1e8d"
	state := recoverySnapshot{UUID: uuid, Snapshot: 9007199254740993, Metadata: fmt.Sprintf("s3://warehouse/lab/%s-%s/metadata/v1.metadata.json", c.Table, uuid)}
	prefix, err := recoveryInspectionDataPrefix(c, state)
	if err != nil || prefix != fmt.Sprintf("s3://warehouse/lab/%s-%s/data/", c.Table, uuid) {
		t.Fatalf("native prefix: %q %v", prefix, err)
	}
	for _, path := range []string{"s3://warehouse/unrelated/metadata/v1.json", "s3://other/lab/table/metadata/v1.json", "s3://warehouse/lab/../metadata/v1.json"} {
		state.Metadata = path
		if _, err = recoveryInspectionDataPrefix(c, state); err == nil {
			t.Fatal("foreign metadata prefix accepted")
		}
	}
}
