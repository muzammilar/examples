package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func historyInput(rows ...RequestRecord) string {
	var output strings.Builder
	encoder := json.NewEncoder(&output)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			panic(err)
		}
	}
	return output.String()
}

func TestAcknowledgedAndUnansweredRequestsStayDistinct(t *testing.T) {
	input := historyInput(
		RequestRecord{Event: "intent", First: 0, Last: 2},
		RequestRecord{Event: "response", First: 0, Last: 2, Outcome: "accepted"},
		RequestRecord{Event: "intent", First: 2, Last: 4},
		RequestRecord{Event: "response", First: 2, Last: 4, Outcome: "unacknowledged"},
		RequestRecord{Event: "intent", First: 4, Last: 6},
	)
	history, err := decodeHistory(strings.NewReader(input), 7)
	if err != nil {
		t.Fatal(err)
	}
	result, err := history.reconcile([]bool{true, false, true, false, false, true, false})
	if err != nil {
		t.Fatal(err)
	}
	if result.Acknowledged != 2 || result.AcknowledgedPresent != 1 || len(result.MissingAcknowledged) != 1 || result.MissingAcknowledged[0] != 1 || result.Ambiguous != 4 || result.AmbiguousPresent != 2 || result.AmbiguousAbsent != 2 {
		t.Fatalf("incorrect acknowledgement reconciliation: %+v", result)
	}
}

func TestCorruptOrOverlappingHistoryCannotAuthorizeAReplay(t *testing.T) {
	for _, rows := range [][]RequestRecord{
		{{Event: "response", First: 0, Last: 1, Outcome: "accepted"}},
		{{Event: "intent", First: 0, Last: 2}, {Event: "intent", First: 1, Last: 3}},
		{{Event: "intent", First: 0, Last: 2}, {Event: "response", First: 0, Last: 1, Outcome: "accepted"}},
		{{Event: "intent", First: 0, Last: 2}, {Event: "response", First: 0, Last: 2, Outcome: "unknown"}},
		{{Event: "intent", First: 0, Last: 2, BodySHA256: "one"}, {Event: "response", First: 0, Last: 2, BodySHA256: "two", Outcome: "accepted"}},
	} {
		if _, err := decodeHistory(strings.NewReader(historyInput(rows...)), 4); err == nil {
			t.Fatal("invalid history accepted", rows)
		}
	}
}

func TestUnattemptedReadbackIsNotAnAmbiguousWrite(t *testing.T) {
	history, err := decodeHistory(strings.NewReader(""), 1)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = history.reconcile([]bool{true}); err == nil {
		t.Fatal("unwitnessed data accepted")
	}
}

func TestReplayEligibilityExcludesNewProcessAndMissingTimestamps(t *testing.T) {
	cutoff := time.Unix(100, 0)
	h := deliveryHistory{accepted: []RequestRecord{
		{First: 0, Last: 2, EndedAt: cutoff.Add(-time.Second)},
		{First: 2, Last: 4, EndedAt: cutoff},
		{First: 4, Last: 6},
	}}
	for id := 0; id <= 6; id++ {
		if actual := h.acknowledgedBefore(id, cutoff); actual != (id < 2) {
			t.Fatalf("id %d eligible=%v", id, actual)
		}
	}
}
