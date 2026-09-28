// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"log/slog"
	"os"

	"github.com/twmb/franz-go/pkg/kgo"
)

// InitLogger creates a structured logger writing to stdout at the given level (debug, info, warn, error)
func InitLogger(level string) *slog.Logger {
	var lvl slog.Level
	if err := lvl.UnmarshalText([]byte(level)); err != nil {
		lvl = slog.LevelInfo
	}
	return slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: lvl}))
}

// KgoLogger returns a franz-go logger (the franz-go internal logs are verbose, so they are opt-in)
func KgoLogger(verbose bool) kgo.Logger {
	level := kgo.LogLevelWarn
	if verbose {
		level = kgo.LogLevelDebug
	}
	return kgo.BasicLogger(os.Stderr, level, nil)
}
