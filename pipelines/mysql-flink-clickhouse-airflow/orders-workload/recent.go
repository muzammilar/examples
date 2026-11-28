package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"
)

type acknowledgedInsert struct {
	ID             int64  `json:"id"`
	Customer       int64  `json:"customer_id"`
	Amount         int64  `json:"amount_cents"`
	Cost           int64  `json:"cost_cents"`
	Second         int64  `json:"event_second"`
	AcknowledgedNS int64  `json:"utc_end_ns"`
	SHA256         string `json:"sha256"`
}

func latestInsert(path string) (*acknowledgedInsert, error) {
	data, err := os.ReadFile(path + "/latest-ack.json")
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var row acknowledgedInsert
	if err = json.Unmarshal(data, &row); err != nil {
		return nil, err
	}
	sum := sha256.Sum256([]byte(fmt.Sprintf("%d:%d:%d:%d:%d", row.ID, row.Customer, row.Amount, row.Cost, row.Second)))
	if row.ID < 1 || row.AcknowledgedNS < 1 || row.SHA256 != hex.EncodeToString(sum[:]) {
		return nil, errors.New("invalid recent-ACK anchor")
	}
	return &row, nil
}

func observeRecent(ctx context.Context, anchor *acknowledgedInsert) (bool, error) {
	began := time.Now()
	r, err := query(ctx, "ch1", fmt.Sprintf("SELECT * FROM pipeline.current_margins WHERE order_id=%d FORMAT JSONCompactEachRow", anchor.ID))
	if err != nil {
		return false, err
	}
	defer r.Body.Close()
	decoder := json.NewDecoder(r.Body)
	var raw []json.RawMessage
	err = decoder.Decode(&raw)
	visible := err == nil
	if err != nil && !errors.Is(err, io.EOF) {
		return false, err
	}
	if visible {
		row, err := decodeMargin(raw)
		if err != nil {
			return false, err
		}
		if row.ID != anchor.ID || row.Customer != anchor.Customer || row.Amount != anchor.Amount || row.Cost != anchor.Cost || row.Second != anchor.Second {
			return false, errors.New("wrong acknowledged immutable insert body")
		}
		var extra []json.RawMessage
		if err = decoder.Decode(&extra); !errors.Is(err, io.EOF) {
			return false, errors.New("duplicate row or malformed recent-read tail")
		}
	}
	state := "pending"
	if visible {
		state = "visible"
	}
	emit(map[string]any{"event": "recent-read", "result": state, "id": anchor.ID, "phase": phase(), "ack_age_ms": float64(time.Now().UnixNano()-anchor.AcknowledgedNS) / float64(time.Millisecond), "duration_ms": float64(time.Since(began)) / float64(time.Millisecond), "utc_ns": time.Now().UnixNano()})
	return visible, nil
}
