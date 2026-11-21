package main

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestCancellationStopsDescendantsHoldingOutput(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	command := managedCommand(ctx, "/bin/sh", "-c", "sleep 30 & wait")
	var output strings.Builder
	command.Stdout, command.Stderr = &output, &output
	started := time.Now()
	err := command.Run()
	if err == nil || ctx.Err() != context.DeadlineExceeded || time.Since(started) > 2*time.Second {
		t.Fatalf("descendant output outlived cancellation: err=%v context=%v elapsed=%s", err, ctx.Err(), time.Since(started))
	}
}
