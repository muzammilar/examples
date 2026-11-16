package main

import (
	"strings"
	"testing"
)

func TestNativeFragmentComesFromScanNotKey(t *testing.T) {
	l := exampleLedger(t, true)
	r := checkReport{Shard: -1}
	input := "ROWID\tcustomer_id\top_id\tamount\n[ fragment: 1 m_page: 0 m_page_idx: 0 ]\t0\t100\t101\n1 rows returned\n"
	n, err := scanNative(strings.NewReader(input), "ROWID\tcustomer_id\top_id\tamount", func(fragment int, v []string) error { r.Shard = fragment; l.observe(&r, 100, 0, 101); return nil })
	if err != nil || n != 1 || r.WrongShard != 1 || r.Wrong != 0 {
		t.Fatalf("native wrong-fragment row passed: %+v %v", r, err)
	}
}
func TestNativeTruncationAndBadFooterFail(t *testing.T) {
	for _, input := range []string{"ROWID\tid\n", "ROWID\tid\n1 rows returned\n", "ROWID\tid\n0 rows returned\nextra\n", "wrong\n0 rows returned\n"} {
		if _, err := scanNative(strings.NewReader(input), "ROWID\tid", func(int, []string) error { return nil }); err == nil {
			t.Fatalf("bad scan passed: %q", input)
		}
	}
}
