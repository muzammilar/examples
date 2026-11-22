package main

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func storageEntry(path string) recoveryManifestEntry {
	content := "EXISTING"
	if expectedNativeVersion == "v2.0.1" {
		content = "DATA"
	}
	return recoveryManifestEntry{Manifest: "s3://warehouse/lab/metadata/manifest.avro", Sequence: 1, ManifestContent: "DATA", Status: "ADDED", Content: content, Path: path, Format: "PARQUET", Records: 10}
}

func TestRecoveryStorageOnlyWarehouseObjectURIs(t *testing.T) {
	for _, value := range []string{"s3://warehouse/lab/file.parquet", "s3://warehouse/lab/a%20b.parquet"} {
		if _, err := recoveryWarehouseURI(value); err != nil {
			t.Fatal(err)
		}
	}
	for _, value := range []string{"s3://other/file", "s3://warehouse/", "file:///tmp/data", "https://storage/file", "s3://key:secret@warehouse/file", "s3://warehouse:8333/file", "s3://warehouse/file?secret=x", "s3://warehouse/file?", "s3://warehouse/file#fragment", "s3://warehouse/file#", "s3://warehouse/file%7f", "s3://warehouse/../file", "s3://warehouse/%2e%2e/file", "s3://warehouse/path\\file", "s3://warehouse/path%00file"} {
		if _, err := recoveryWarehouseURI(value); err == nil {
			t.Errorf("accepted unsafe URI %q", value)
		}
	}
	for _, suffix := range []string{"*", "?", "[a]", "{a,b}", "%2a", "%3f", "%5ba%5d", "%7ba,b%7d"} {
		if _, err := recoveryWarehouseURI("s3://warehouse/part" + suffix + ".parquet"); err == nil {
			t.Errorf("accepted multi-file glob URI %q", suffix)
		}
	}
}

func TestRecoveryStorageManifestLabelsAndOldManifestRemainLive(t *testing.T) {
	old := storageEntry("s3://warehouse/old.parquet")
	old.Sequence = 0
	old.Status = "EXISTING"
	latest := storageEntry("s3://warehouse/latest.parquet")
	latest.Sequence = 99
	deleted := storageEntry("s3://warehouse/deleted.parquet")
	deleted.Status = "DELETED"
	live, count, err := recoveryLiveFiles([]recoveryManifestEntry{old, latest, deleted})
	if err != nil || len(live) != 2 || count != 1 || live[0].Path != old.Path {
		t.Fatalf("discarded live old manifest: %+v %d %v", live, count, err)
	}
	for _, mutation := range []func(*recoveryManifestEntry){
		func(e *recoveryManifestEntry) { e.Status = "UNKNOWN" }, func(e *recoveryManifestEntry) { e.ManifestContent = "UNKNOWN" }, func(e *recoveryManifestEntry) {
			if expectedNativeVersion == "v2.0.1" {
				e.Content = "EXISTING"
			} else {
				e.Content = "DATA"
			}
		}, func(e *recoveryManifestEntry) { e.Content = "POSITION_DELETES" }, func(e *recoveryManifestEntry) { e.Content = "EQUALITY_DELETES" }, func(e *recoveryManifestEntry) { e.Content = "ADDED" }, func(e *recoveryManifestEntry) { e.Content = "DELETED" }, func(e *recoveryManifestEntry) { e.ManifestContent = "DELETE" }, func(e *recoveryManifestEntry) { e.Format = "AVRO" }, func(e *recoveryManifestEntry) { e.Records = -1 }, func(e *recoveryManifestEntry) { e.Manifest = "https://not-the-lab.invalid/m" },
	} {
		bad := old
		mutation(&bad)
		if _, _, err := recoveryLiveFiles([]recoveryManifestEntry{bad}); err == nil {
			t.Errorf("accepted unsupported manifest %+v", bad)
		}
	}
	duplicate := old
	duplicate.Path = "s3://warehouse/%6fld.parquet"
	if _, _, err := recoveryLiveFiles([]recoveryManifestEntry{old, duplicate}); err == nil {
		t.Fatal("accepted duplicate decoded object URI")
	}
}

func TestRecoveryStorageParquetFormatCasePreservesRawLabel(t *testing.T) {
	for _, format := range []string{"parquet", "PARQUET", "ParQuEt"} {
		entry := storageEntry("s3://warehouse/data.parquet")
		entry.Format = format
		live, deleted, err := recoveryLiveFiles([]recoveryManifestEntry{entry})
		if err != nil || len(live) != 1 || deleted != 0 || live[0].Format != format {
			t.Fatalf("format %q changed or rejected: %+v, %d, %v", format, live, deleted, err)
		}
	}
	for _, format := range []string{"AVRO", "avro", "ORC", "orc", "", "unknown", "parquet ", " parquet", "PARQUET-extra"} {
		entry := storageEntry("s3://warehouse/data.parquet")
		entry.Format = format
		if _, _, err := recoveryLiveFiles([]recoveryManifestEntry{entry}); err == nil {
			t.Errorf("accepted unsupported format %q", format)
		}
	}
}

func TestRecoveryStorageCheckedTotalsAndCompressionDenominator(t *testing.T) {
	file := recoveryParquetFile{Path: "s3://warehouse/a.parquet", ManifestRecords: 10, Rows: 10, Bytes: 1200, Footer: 200, Groups: []recoveryChunkGroup{{Compression: "ZSTD", Encodings: "PLAIN, RLE", Chunks: 14, Compressed: 1000, Uncompressed: 4000}}}
	totals, ratio, err := recoveryStorageSummary([]recoveryParquetFile{file}, 10)
	if err != nil || ratio != 4 || totals.Rows != 10 || totals.Compressed != 1000 || totals.Uncompressed != 4000 || totals.Bytes != 1200 || totals.Footer != 200 || totals.Chunks != 14 {
		t.Fatalf("wrong native chunk totals %+v ratio=%v err=%v", totals, ratio, err)
	}
	if _, _, err := recoveryStorageSummary(nil, 10); err == nil {
		t.Fatal("nonempty oracle passed with no files")
	}
	if _, _, err := recoveryStorageSummary([]recoveryParquetFile{file}, 11); err == nil {
		t.Fatal("file rows differed from full-body oracle")
	}
	for _, mutate := range []func(*recoveryParquetFile){func(f *recoveryParquetFile) { f.Rows = 9 }, func(f *recoveryParquetFile) { f.Footer = f.Bytes }, func(f *recoveryParquetFile) { f.Groups = nil }, func(f *recoveryParquetFile) { f.Groups[0].Compressed = 0 }, func(f *recoveryParquetFile) { f.Groups[0].Uncompressed = -1 }, func(f *recoveryParquetFile) { f.Groups[0].Compression = "" }, func(f *recoveryParquetFile) { f.Groups[0].Chunks = 0 }} {
		bad := file
		bad.Groups = append([]recoveryChunkGroup(nil), file.Groups...)
		mutate(&bad)
		if _, _, err := recoveryStorageSummary([]recoveryParquetFile{bad}, 10); err == nil {
			t.Fatal("accepted missing/invalid compression measurement")
		}
	}
	n := int64(math.MaxInt64)
	if err := recoveryAddStorage(&n, 1); err == nil {
		t.Fatal("overflow was not rejected")
	}
	if n != math.MaxInt64 {
		t.Fatal("overflow mutated accumulated result")
	}
	overflow := file
	overflow.Bytes = math.MaxInt64
	if _, _, err := recoveryStorageSummary([]recoveryParquetFile{overflow, file}, 20); err == nil {
		t.Fatal("aggregate overflow accepted")
	}
}

// The full containerized package registers the pinned native driver in main.go.
// Explicit stdlib-only file-list runs skip this control rather than claiming it.
func TestRecoveryStorageNativeLocalParquetSQL(t *testing.T) {
	registered := false
	for _, driver := range sql.Drivers() {
		registered = registered || driver == "duckdb"
	}
	if !registered {
		t.Skip("native DuckDB driver excluded from this stdlib-only run")
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	for _, setting := range []string{"SET threads=1", "SET memory_limit='128MB'", "SET autoinstall_known_extensions=false"} {
		if _, err = db.ExecContext(ctx, setting); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(t.TempDir(), "compression.parquet")
	query := fmt.Sprintf("COPY (SELECT i::BIGINT event_id,repeat('compressible-payload',32) payload FROM range(4096) t(i)) TO '%s' (FORMAT PARQUET,COMPRESSION SNAPPY,ROW_GROUP_SIZE 2048)", strings.ReplaceAll(path, "'", "''"))
	if _, err = db.ExecContext(ctx, query); err != nil {
		t.Fatal(err)
	}
	file, err := recoveryParquetMetadata(ctx, db, path, 4096)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if file.Bytes != info.Size() {
		t.Fatal("native Parquet size differs from filesystem fixture")
	}
	totals, ratio, err := recoveryStorageSummary([]recoveryParquetFile{file}, 4096)
	if err != nil {
		t.Fatal(err)
	}
	var chunks, compressed, uncompressed int64
	if err = db.QueryRowContext(ctx, "SELECT count(*)::BIGINT,sum(total_compressed_size)::BIGINT,sum(total_uncompressed_size)::BIGINT FROM parquet_metadata(?)", path).Scan(&chunks, &compressed, &uncompressed); err != nil {
		t.Fatal(err)
	}
	if totals.Chunks != chunks || totals.Compressed != compressed || totals.Uncompressed != uncompressed || chunks != 4 || ratio <= 1 {
		t.Fatalf("native rowgroup/column aggregation wrong: %+v ratio=%v", totals, ratio)
	}
	t.Logf("native local Parquet control only: rows=%d chunks=%d file_bytes=%d footer_bytes=%d compressed_chunk_bytes=%d uncompressed_chunk_bytes=%d encoded_page_ratio=%.6f", totals.Rows, totals.Chunks, totals.Bytes, totals.Footer, totals.Compressed, totals.Uncompressed, ratio)
	for _, group := range file.Groups {
		if group.Compression != "SNAPPY" || group.Encodings == "" {
			t.Fatalf("unexpected native codec/encoding %+v", group)
		}
	}
	if _, err := recoveryParquetMetadata(ctx, db, path, 4095); err == nil {
		t.Fatal("native manifest rowcount mismatch accepted")
	}
}

func TestRecoveryStorageContentEnumsStayVersionQualified(t *testing.T) {
	for _, lane := range []struct{ engine, good, bad string }{{"v1.5.5", "EXISTING", "DATA"}, {"v2.0.1", "DATA", "EXISTING"}} {
		entry := storageEntry("s3://warehouse/data.parquet")
		entry.Content = lane.good
		if _, _, err := recoveryLiveFilesForVersion([]recoveryManifestEntry{entry}, lane.engine); err != nil {
			t.Fatal(lane.engine, err)
		}
		entry.Content = lane.bad
		if _, _, err := recoveryLiveFilesForVersion([]recoveryManifestEntry{entry}, lane.engine); err == nil {
			t.Fatal("accepted wrong engine content enum", lane.engine)
		}
		entry.Status = "DELETED"
		if _, _, err := recoveryLiveFilesForVersion([]recoveryManifestEntry{entry}, lane.engine); err == nil {
			t.Fatal("discarded unrecognized deleted-file content", lane.engine)
		}
	}
	if _, _, err := recoveryLiveFilesForVersion(nil, "v2.1.0"); err == nil {
		t.Fatal("accepted unqualified engine semantics")
	}
}
