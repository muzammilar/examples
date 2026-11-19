package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func run() error {
	if len(os.Args) != 2 || (os.Args[1] != "writer" && os.Args[1] != "reader") {
		return fmt.Errorf("expected writer or reader")
	}
	role := os.Args[1]
	root := "/results"
	seconds, err := boundedInteger("DURATION_SECONDS", 1800, 60, 7200)
	if err != nil {
		return err
	}
	batch, err := boundedInteger("UPDATE_BATCH", 10, 1, 100)
	if err != nil {
		return err
	}
	fixture, err := loadFixture("/corpus")
	if err != nil {
		return err
	}
	data, err := os.ReadFile(filepath.Join(root, "case.json"))
	if err != nil {
		return err
	}
	var plan struct {
		StartVersion int `json:"start_version"`
	}
	if err = json.Unmarshal(data, &plan); err != nil {
		return err
	}
	if plan.StartVersion < 1 || plan.StartVersion >= 100000000 {
		return fmt.Errorf("invalid starting version")
	}
	client, err := newClient()
	if err != nil {
		return err
	}
	defer client.http.CloseIdleConnections()
	journal, err := openJournal(filepath.Join(root, role+".jsonl"))
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(seconds)*time.Second)
	defer cancel()
	if role == "writer" {
		err = writeInventory(ctx, root, fixture, client, journal, plan.StartVersion, batch)
	} else {
		err = readInventory(ctx, root, fixture, client, journal)
	}
	return errors.Join(err, journal.file.Close())
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
