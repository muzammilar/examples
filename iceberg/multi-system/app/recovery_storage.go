package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"
	"net/url"
	"strings"
)

type recoveryManifestEntry struct {
	Manifest        string `json:"manifest_path"`
	Sequence        int64  `json:"manifest_sequence_number"`
	ManifestContent string `json:"manifest_content"`
	Status          string `json:"status"`
	Content         string `json:"content"`
	Path            string `json:"file_path"`
	Format          string `json:"file_format"`
	Records         int64  `json:"record_count"`
}
type recoveryChunkGroup struct {
	Compression  string `json:"compression"`
	Encodings    string `json:"encodings"`
	Chunks       int64  `json:"column_chunks"`
	Compressed   int64  `json:"compressed_bytes"`
	Uncompressed int64  `json:"uncompressed_bytes"`
}
type recoveryParquetFile struct {
	Path            string               `json:"path"`
	ManifestRecords int64                `json:"manifest_record_count"`
	Rows            int64                `json:"rows"`
	Bytes           int64                `json:"file_bytes"`
	Footer          int64                `json:"footer_bytes"`
	Groups          []recoveryChunkGroup `json:"groups"`
}
type recoveryStorageTotals struct {
	Files        int64 `json:"files"`
	Rows         int64 `json:"rows"`
	Bytes        int64 `json:"file_bytes"`
	Footer       int64 `json:"footer_bytes"`
	Chunks       int64 `json:"column_chunks"`
	Compressed   int64 `json:"compressed_chunk_bytes"`
	Uncompressed int64 `json:"uncompressed_chunk_bytes"`
}
type recoveryStorageMetrics struct {
	Entries         []recoveryManifestEntry `json:"raw_manifest_entries"`
	ExcludedDeleted int64                   `json:"excluded_deleted_entries"`
	Files           []recoveryParquetFile   `json:"files"`
	Totals          recoveryStorageTotals   `json:"totals"`
	Ratio           float64                 `json:"encoded_parquet_chunk_compression_ratio"`
}

// Return decoded object identity for duplicate checks, while retaining the exact
// catalog URI for native reads. No endpoint, credential, or local-path discovery.
func recoveryWarehouseURI(value string) (string, error) {
	u, err := url.Parse(value)
	if err != nil || u.Scheme != "s3" || u.Host != "warehouse" || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.ForceQuery || strings.Contains(value, "#") || u.Path == "" || u.Path == "/" || !strings.HasPrefix(u.Path, "/") || strings.ContainsAny(u.Path, "\\*?[]{}") {
		return "", errors.New("only explicit s3://warehouse/ object URIs without credentials/query/fragment are allowed")
	}
	for _, r := range u.Path {
		if r < 32 || r == 127 {
			return "", errors.New("control character in object URI")
		}
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return "", errors.New("object URI contains traversal")
		}
	}
	return u.Path, nil
}

func recoveryAddStorage(total *int64, value int64) error {
	if value < 0 || *total < 0 || value > math.MaxInt64-*total {
		return errors.New("negative or overflowing native storage counter")
	}
	*total += value
	return nil
}

func recoveryLiveFiles(entries []recoveryManifestEntry) ([]recoveryManifestEntry, int64, error) {
	return recoveryLiveFilesForVersion(entries, expectedNativeVersion)
}

func recoveryLiveFilesForVersion(entries []recoveryManifestEntry, engine string) ([]recoveryManifestEntry, int64, error) {
	dataContent := ""
	knownContent := map[string]bool{"POSITION_DELETES": true, "EQUALITY_DELETES": true}
	switch engine {
	case "v1.5.5":
		// Iceberg 45163a28 incorrectly formats the content enum as entry status.
		dataContent = "EXISTING"
		knownContent["EXISTING"], knownContent["ADDED"], knownContent["DELETED"] = true, true, true
	case "v2.0.1":
		// Iceberg f979b5b28a correctly reports the file-content enum.
		dataContent, knownContent["DATA"] = "DATA", true
	default:
		return nil, 0, fmt.Errorf("unqualified manifest content semantics for DuckDB %s", engine)
	}
	var live []recoveryManifestEntry
	var deleted int64
	seen := map[string]bool{}
	for _, entry := range entries {
		if _, err := recoveryWarehouseURI(entry.Manifest); err != nil {
			return nil, 0, err
		}
		identity, err := recoveryWarehouseURI(entry.Path)
		if err != nil {
			return nil, 0, err
		}
		if entry.Sequence < 0 || entry.Records < 0 || (entry.Status != "EXISTING" && entry.Status != "ADDED" && entry.Status != "DELETED") || (entry.ManifestContent != "DATA" && entry.ManifestContent != "DELETE") || !knownContent[entry.Content] || !strings.EqualFold(entry.Format, "PARQUET") {
			return nil, 0, fmt.Errorf("unknown/unsupported native manifest entry: %+v", entry)
		}
		if entry.Status == "DELETED" {
			deleted++
			continue
		}
		if entry.ManifestContent != "DATA" || entry.Content != dataContent {
			return nil, 0, errors.New("active delete file or unsupported content in append-only snapshot")
		}
		if seen[identity] {
			return nil, 0, errors.New("duplicate live Parquet URI")
		}
		seen[identity] = true
		live = append(live, entry)
	}
	return live, deleted, nil
}

// Caller validates catalog URIs first; the local native SQL control invokes
// this helper directly on its own temporary Parquet fixture, without any S3.
func recoveryParquetMetadata(ctx context.Context, db *sql.DB, path string, records int64) (recoveryParquetFile, error) {
	file := recoveryParquetFile{Path: path, ManifestRecords: records}
	if err := db.QueryRowContext(ctx, "SELECT file_size_bytes::BIGINT,num_rows,footer_size::BIGINT FROM parquet_file_metadata(?)", path).Scan(&file.Bytes, &file.Rows, &file.Footer); err != nil {
		return file, err
	}
	if file.Rows != records || file.Rows < 0 || file.Bytes <= 0 || file.Footer <= 0 || file.Footer >= file.Bytes {
		return file, errors.New("Parquet file metadata differs from manifest or lacks sizes")
	}
	rows, err := db.QueryContext(ctx, "SELECT compression,encodings,count(*)::BIGINT,sum(total_compressed_size)::BIGINT,sum(total_uncompressed_size)::BIGINT FROM parquet_metadata(?) GROUP BY compression,encodings ORDER BY compression,encodings", path)
	if err != nil {
		return file, err
	}
	defer rows.Close()
	for rows.Next() {
		var group recoveryChunkGroup
		if err = rows.Scan(&group.Compression, &group.Encodings, &group.Chunks, &group.Compressed, &group.Uncompressed); err != nil {
			return file, err
		}
		file.Groups = append(file.Groups, group)
	}
	if err = rows.Err(); err != nil {
		return file, err
	}
	return file, nil
}

func recoveryStorageSummary(files []recoveryParquetFile, expectedRows int64) (recoveryStorageTotals, float64, error) {
	var totals recoveryStorageTotals
	for _, file := range files {
		if file.Rows != file.ManifestRecords || file.Rows < 0 || file.Bytes <= file.Footer || file.Footer <= 0 || len(file.Groups) == 0 {
			return totals, 0, errors.New("incomplete native Parquet measurement")
		}
		for _, value := range []struct {
			dst *int64
			n   int64
		}{{&totals.Files, 1}, {&totals.Rows, file.Rows}, {&totals.Bytes, file.Bytes}, {&totals.Footer, file.Footer}} {
			if err := recoveryAddStorage(value.dst, value.n); err != nil {
				return totals, 0, err
			}
		}
		for _, group := range file.Groups {
			if group.Compression == "" || group.Encodings == "" || group.Chunks <= 0 || group.Compressed <= 0 || group.Uncompressed <= 0 {
				return totals, 0, errors.New("missing Parquet codec/chunk compression measurement")
			}
			for _, value := range []struct {
				dst *int64
				n   int64
			}{{&totals.Chunks, group.Chunks}, {&totals.Compressed, group.Compressed}, {&totals.Uncompressed, group.Uncompressed}} {
				if err := recoveryAddStorage(value.dst, value.n); err != nil {
					return totals, 0, err
				}
			}
		}
	}
	if expectedRows < 0 || totals.Rows != expectedRows || totals.Files == 0 || totals.Compressed <= 0 {
		return totals, 0, errors.New("live Parquet row/file count differs from full-body oracle or measurement is absent")
	}
	return totals, float64(totals.Uncompressed) / float64(totals.Compressed), nil
}

func recoveryMeasureStorage(ctx context.Context, db *sql.DB, metadata string, snapshot, expectedRows int64) (recoveryStorageMetrics, error) {
	var result recoveryStorageMetrics
	if _, err := recoveryWarehouseURI(metadata); err != nil {
		return result, err
	}
	if snapshot <= 0 {
		return result, errors.New("explicit committed snapshot ID required")
	}
	rows, err := db.QueryContext(ctx, "SELECT manifest_path,manifest_sequence_number,manifest_content,status,content,file_path,file_format,record_count FROM iceberg_metadata(?,snapshot_from_id=CAST(? AS UBIGINT))", metadata, snapshot)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var e recoveryManifestEntry
		if err = rows.Scan(&e.Manifest, &e.Sequence, &e.ManifestContent, &e.Status, &e.Content, &e.Path, &e.Format, &e.Records); err != nil {
			break
		}
		result.Entries = append(result.Entries, e)
	}
	if err == nil {
		err = rows.Err()
	}
	closeErr := rows.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return result, err
	}
	// MaxOpenConns=1: enumeration must close before any nested per-file query.
	live, deleted, err := recoveryLiveFiles(result.Entries)
	if err != nil {
		return result, err
	}
	result.ExcludedDeleted = deleted
	for _, entry := range live {
		file, err := recoveryParquetMetadata(ctx, db, entry.Path, entry.Records)
		if err != nil {
			return result, err
		}
		result.Files = append(result.Files, file)
	}
	result.Totals, result.Ratio, err = recoveryStorageSummary(result.Files, expectedRows)
	return result, err
}
