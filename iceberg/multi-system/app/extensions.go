package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
)

// The dynamic-library lane sets this with -X main.expectedNativeVersion=v2.0.1.
// Never infer qualification from the Go driver's version or silently upgrade.
var expectedNativeVersion = "v1.5.5"

type qualifiedExtension struct {
	Name      string `json:"name"`
	Version   string `json:"version"`
	Path      string `json:"install_path"`
	SHA256    string `json:"sha256"`
	Installed bool   `json:"installed"`
	Loaded    bool   `json:"loaded"`
}

func qualifiedExtensionPins(actual, expected string) ([]qualifiedExtension, error) {
	if actual != expected {
		return nil, fmt.Errorf("native DuckDB %s differs from expected %s before extension installation", actual, expected)
	}
	switch actual {
	case "v1.5.5":
		return []qualifiedExtension{{Name: "avro", Version: "f9d5902"}, {Name: "httpfs", Version: "827222f"}, {Name: "iceberg", Version: "45163a28"}}, nil
	case "v2.0.1":
		return []qualifiedExtension{
			{Name: "avro", Version: "4176191c96", SHA256: "be6f927c2bcf25fe4da383bc2e276fcd91a74be8da217704685d5a0910a7b7ed"},
			{Name: "httpfs", Version: "f71cfdb4bf", SHA256: "d8bb1c0ecb28ca10c6be7d293f1518875bd1f0054beda8b5ffdde3b4436cbac3"},
			{Name: "iceberg", Version: "f979b5b28a", SHA256: "91981c5b2091ae22179232643862f6929031b7f911a5aea075d8df1e1c4a3c60"},
		}, nil
	default:
		return nil, fmt.Errorf("native DuckDB %s has no qualified extension pin set", actual)
	}
}

func checkQualifiedExtensions(pins, observed []qualifiedExtension) error {
	if len(observed) != len(pins) {
		return errors.New("qualified extension set is incomplete or duplicated")
	}
	for i, pin := range pins {
		value := observed[i] // Both the declared pins and SQL rows are sorted by name.
		if value.Name != pin.Name || value.Version != pin.Version || (pin.SHA256 != "" && value.SHA256 != pin.SHA256) {
			return fmt.Errorf("extension %s revision/binary differs from qualified pins", pin.Name)
		}
	}
	return nil
}

func qualifiedExtensionHash(path string) (string, error) {
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", err
	}
	root, err := filepath.EvalSymlinks("/opt/duckdb/extensions")
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, resolved)
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", errors.New("native extension file is outside the owned extension directory")
	}
	return recoveryFileHash(resolved)
}

// Observe the active connection without installing or loading anything. The
// report describes this verification, independently of a case's seed-time lane.
func qualifiedNativeVersions(ctx context.Context, db *sql.DB) (map[string]any, error) {
	var engine, platform string
	if err := db.QueryRowContext(ctx, "SELECT version()").Scan(&engine); err != nil {
		return nil, err
	}
	pins, err := qualifiedExtensionPins(engine, expectedNativeVersion)
	if err != nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx, "SELECT extension_name,extension_version,install_path,installed,loaded FROM duckdb_extensions() WHERE installed AND extension_name IN ('avro','httpfs','iceberg') ORDER BY extension_name")
	if err != nil {
		return nil, err
	}
	var observed []qualifiedExtension
	for rows.Next() {
		var value qualifiedExtension
		if err = rows.Scan(&value.Name, &value.Version, &value.Path, &value.Installed, &value.Loaded); err == nil {
			value.SHA256, err = qualifiedExtensionHash(value.Path)
		}
		if err != nil {
			return nil, errors.Join(err, rows.Close())
		}
		observed = append(observed, value)
	}
	if err = errors.Join(rows.Err(), rows.Close(), checkQualifiedExtensions(pins, observed)); err != nil {
		return nil, err
	}
	if err = db.QueryRowContext(ctx, "PRAGMA platform").Scan(&platform); err != nil {
		return nil, err
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	binary, err := recoveryFileHash(executable)
	if err != nil {
		return nil, err
	}
	versions := map[string]any{"engine": engine, "platform": platform, "extensions": observed, "lab_binary_sha256": binary}
	driver := "unavailable"
	if build, ok := debug.ReadBuildInfo(); ok {
		for _, dependency := range build.Deps {
			if dependency.Path == "github.com/duckdb/duckdb-go/v2" {
				driver = dependency.Version
				if dependency.Replace != nil {
					driver = dependency.Replace.Version
					if driver == "" {
						driver = "local-replacement"
					}
				}
			}
		}
	}
	versions["duckdb_go_version"] = driver
	return versions, nil
}

func installQualifiedExtensions() (returned error) {
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return err
	}
	defer func() { returned = errors.Join(returned, db.Close()) }()
	db.SetMaxOpenConns(1)
	var engine string
	if err = db.QueryRow("SELECT version()").Scan(&engine); err != nil {
		return err
	}
	if _, err = qualifiedExtensionPins(engine, expectedNativeVersion); err != nil {
		return err
	}
	for _, query := range []string{"SET extension_directory='/opt/duckdb/extensions'", "FORCE INSTALL httpfs FROM 'https://extensions.duckdb.org'", "FORCE INSTALL avro FROM 'https://extensions.duckdb.org'", "FORCE INSTALL iceberg FROM 'https://extensions.duckdb.org'", "LOAD iceberg"} {
		if _, err = db.Exec(query); err != nil {
			return err
		}
	}
	versions, err := qualifiedNativeVersions(context.Background(), db)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(versions)
}
