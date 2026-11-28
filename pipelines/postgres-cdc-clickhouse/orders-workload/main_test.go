package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestDecodeExactNativeRow(t *testing.T) {
	var raw []json.RawMessage
	if err := json.Unmarshal([]byte(`["1",2,"3","r4",5,"6",7,"5000006"]`), &raw); err != nil {
		t.Fatal(err)
	}
	got, err := decodeRow(raw)
	if err != nil {
		t.Fatal(err)
	}
	want := row{1, 2, 3, "r4", 5, 6, 7, 5000006}
	if got != want {
		t.Fatalf("got %+v want %+v", got, want)
	}
}
func TestInvalidRowsAreNotSuccess(t *testing.T) {
	for _, input := range []string{`[1,2,3,null,5,6,7,8]`, `[1,2,3,"r4",5,6,7,"9223372036854775808"]`, `[1,2,3,"r4",5,6,7.1,8]`, `[1,2,3,"r4",5,6,7]`} {
		var raw []json.RawMessage
		if err := json.Unmarshal([]byte(input), &raw); err != nil {
			t.Fatal(err)
		}
		if _, err := decodeRow(raw); err == nil {
			t.Fatalf("accepted invalid row %s", input)
		}
	}
}

func TestPartialSnapshotGapsAreNotWrongValues(t *testing.T) {
	// Snapshot chunks can arrive from different Kafka partitions out of order.
	count, err := readImmutable(strings.NewReader(`[501,501,1501,"r1",1,1,1700000501,1000001]
[503,503,1503,"r3",1,1,1700000503,1000001]`), 501, 10000)
	if err != nil || count != 2 {
		t.Fatalf("count=%d error=%v", count, err)
	}
}
func TestImmutableReaderRejectsWrongValuesAndDuplicates(t *testing.T) {
	for _, body := range []string{
		`[501,501,9999,"r1",1,1,1700000501,1000001]`,
		`[501,501,1501,"r1",1,1,1700000501,1000001]
[501,501,1501,"r1",1,1,1700000501,1000001]`,
	} {
		if _, err := readImmutable(strings.NewReader(body), 501, 10000); err == nil {
			t.Fatal("invalid rows accepted")
		}
	}
}
