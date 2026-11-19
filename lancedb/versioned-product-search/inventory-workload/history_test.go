package main

import (
	"encoding/json"
	"os"
	"testing"
)

func baseline() *Fixture {
	return &Fixture{Products: []Product{{ID: "0", Tenant: 1, Stock: 5, PriceCents: 100, Version: 0}}}
}
func TestHistoryRejectsWrongBodiesAndAcknowledgedRegression(t *testing.T) {
	f := baseline()
	p, _ := f.product("0", 4)
	h := &history{acks: map[string]int{"0": 4}, intents: map[string]map[int]Product{"0": {4: p}}}
	if err := h.check(f, p, 4); err != nil {
		t.Fatal(err)
	}
	if err := h.check(f, f.Products[0], 4); err == nil {
		t.Fatal("accepted acknowledged regression")
	}
	wrong := p
	wrong.Tenant = 2
	if err := h.check(f, wrong, 0); err == nil {
		t.Fatal("accepted tenant leak")
	}
	wrong = p
	wrong.PriceCents++
	if err := h.check(f, wrong, 0); err == nil {
		t.Fatal("accepted torn body")
	}
	unwitnessed, _ := f.product("0", 5)
	if err := h.check(f, unwitnessed, 0); err == nil {
		t.Fatal("accepted unknown version")
	}
	if err := h.check(f, p, 0); err != nil {
		t.Fatal("an ambiguous witnessed body is allowed without manufacturing an ACK", err)
	}
}
func TestHistoryTailsOnlyCompleteLines(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/writer.jsonl"
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	p, _ := baseline().product("0", 1)
	data, _ := json.Marshal(Event{Stage: "attempt", Rows: []Product{p}})
	split := len(data) / 2
	if _, err = f.Write(data[:split]); err != nil {
		t.Fatal(err)
	}
	h := &history{acks: make(map[string]int), intents: make(map[string]map[int]Product)}
	if err = h.consume(dir); err != nil {
		t.Fatal(err)
	}
	defer h.file.Close()
	if len(h.intents) != 0 || h.offset != 0 {
		t.Fatal("consumed partial witness")
	}
	f.Write(append(data[split:], '\n'))
	if err = h.consume(dir); err != nil {
		t.Fatal(err)
	}
	if h.intents["0"][1] != p || len(h.acks) != 0 {
		t.Fatal("intent became ACK")
	}
	outcome, _ := json.Marshal(Event{Stage: "ambiguous", Rows: []Product{p}})
	f.Write(append(outcome, '\n'))
	if err = h.consume(dir); err != nil {
		t.Fatal(err)
	}
	if len(h.acks) != 0 {
		t.Fatal("ambiguous outcome became ACK")
	}
}
func TestHistoryRejectsOrphanAckAndTruncation(t *testing.T) {
	for _, input := range []string{"{\"stage\":\"ack\",\"rows\":[{\"id\":\"0\",\"version\":9}]}\n", "{\"stage\":\"attempt\""} {
		f, err := os.CreateTemp(t.TempDir(), "witness")
		if err != nil {
			t.Fatal(err)
		}
		f.WriteString(input)
		f.Seek(0, 0)
		h := &history{acks: make(map[string]int), intents: make(map[string]map[int]Product)}
		if err = h.consumeFile(f, true); err == nil {
			t.Fatal("accepted corrupt historical witness")
		}
		f.Close()
	}
}
