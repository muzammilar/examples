package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestDecodeExactMarginAndRejectInvalidIntegers(t *testing.T) {
	var raw []json.RawMessage
	if err := json.Unmarshal([]byte(`["501",501,"1501",601,"0","r1",300,"855",1,"1",1700000501]`), &raw); err != nil {
		t.Fatal(err)
	}
	got, err := decodeMargin(raw)
	if err != nil || got.ID != 501 || got.Margin != 855 || got.Region != "r1" {
		t.Fatalf("got %+v: %v", got, err)
	}
	for _, invalid := range []string{"null", "1.5", "9223372036854775808"} {
		copyRow := append([]json.RawMessage(nil), raw...)
		copyRow[0] = json.RawMessage(invalid)
		if _, err = decodeMargin(copyRow); err == nil {
			t.Fatalf("accepted invalid integer %s", invalid)
		}
	}
}

func TestImmutableSnapshotValidatesActualIDsDespiteGaps(t *testing.T) {
	// Discount is in basis points; financial arithmetic stays in integer cents.
	count, err := readImmutable(strings.NewReader(`[501,501,1501,601,0,"r1",300,855,1,1,1700000501]
[503,503,1503,603,0,"r3",500,825,1,1,1700000503]`), 501, 10000)
	if err != nil || count != 2 {
		t.Fatalf("rows=%d err=%v", count, err)
	}
}

func TestImmutableReaderRejectsWrongMoneyAndDuplicateRows(t *testing.T) {
	for _, body := range []string{
		`[501,501,1501,601,0,"r1",300,856,1,1,1700000501]`,
		`[501,501,1501,601,0,"r1",300,855,1,1,1700000501]
[501,501,1501,601,0,"r1",300,855,1,1,1700000501]`,
	} {
		if _, err := readImmutable(strings.NewReader(body), 501, 10000); err == nil {
			t.Fatal("accepted corrupt/duplicate row")
		}
	}
}
