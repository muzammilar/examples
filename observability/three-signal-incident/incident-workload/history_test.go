package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func historyForTest(t *testing.T, rows []RequestRecord) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "requests.jsonl")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		if err = json.NewEncoder(file).Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	if err = file.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAmbiguousTraceBatchExpandsToThreeSpansPerRequest(t *testing.T) {
	path := historyForTest(t, []RequestRecord{{Event: "intent", Signal: "traces", First: 0, Last: 2, RawSHA256: "same"}, {Event: "response", Signal: "traces", First: 0, Last: 2, RawSHA256: "same", Outcome: "unacknowledged"}})
	h, err := readHistory(path, "traces", 2)
	if err != nil {
		t.Fatal(err)
	}
	result, err := h.reconcile([]int{2, 1, 1, 1, 1, 1})
	if err != nil || result.AmbiguousPresent != 6 || result.Acknowledged != 0 || result.DuplicateCopies != 1 {
		t.Fatalf("result=%+v error=%v", result, err)
	}
}

func TestCorruptResponseCannotAuthorizeAnAcknowledgement(t *testing.T) {
	path := historyForTest(t, []RequestRecord{{Event: "intent", Signal: "logs", First: 0, Last: 1, RawSHA256: "original"}, {Event: "response", Signal: "logs", First: 0, Last: 1, RawSHA256: "changed", Outcome: "accepted"}})
	if _, err := readHistory(path, "logs", 1); err == nil {
		t.Fatal("response changed the witnessed body")
	}
	if _, err := (&history{states: []delivery{unattempted}}).reconcile([]int{1}); err == nil {
		t.Fatal("unattempted data appeared without a witness")
	}
}
