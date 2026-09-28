package common

import (
	"reflect"
	"testing"
)

func TestNewMessage(t *testing.T) {
	for _, id := range []int{0, 1, UserIDMin, len(Trees), UserIDMax - 1} {
		m := NewMessage(id, ProducerSync)
		if m.UserId != id || m.Async != ProducerSync || m.Data != Trees[id%len(Trees)] {
			t.Errorf("NewMessage(%d) = %+v", id, m)
		}
	}
}

func TestMessageRoundTrip(t *testing.T) {
	m := NewMessage(123, ProducerAsync)
	b, err := m.Encode()
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"Async":1,"UserId":123,"Data":"` + Trees[123%len(Trees)] + `"}`; string(b) != want {
		t.Fatalf("Encode() = %s, want %s", b, want)
	}
	got, err := DecodeMessage(b)
	if err != nil {
		t.Fatal(err)
	}
	if got != m {
		t.Fatalf("got %+v, want %+v", got, m)
	}
}

func TestDecodeMessageInvalid(t *testing.T) {
	for _, in := range []string{"", "not json", `{"UserId":"abc"}`} {
		if _, err := DecodeMessage([]byte(in)); err == nil {
			t.Errorf("DecodeMessage(%q) expected an error", in)
		}
	}
}

func TestNewRecord(t *testing.T) {
	r, err := NewRecord("trees", 4242, ProducerSync)
	if err != nil {
		t.Fatal(err)
	}
	if r.Topic != "trees" || string(r.Key) != "4242" {
		t.Fatalf("unexpected record topic/key: %q/%q", r.Topic, r.Key)
	}
	m, err := DecodeMessage(r.Value)
	if err != nil || m != NewMessage(4242, ProducerSync) {
		t.Fatalf("unexpected record value %s (%v)", r.Value, err)
	}
}

func TestSplitList(t *testing.T) {
	tests := map[string][]string{
		"":              nil,
		" , ,":          nil,
		"a":             {"a"},
		" a, b,,c ,":    {"a", "b", "c"},
		"host:1,host:2": {"host:1", "host:2"},
	}
	for in, want := range tests {
		if got := SplitList(in); !reflect.DeepEqual(got, want) {
			t.Errorf("SplitList(%q) = %v, want %v", in, got, want)
		}
	}
}

/*
 * Benchmarks
 */

func BenchmarkMessageEncode(b *testing.B) {
	m := NewMessage(4242, ProducerSync)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if _, err := m.Encode(); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkMessageDecode(b *testing.B) {
	buf, err := NewMessage(4242, ProducerSync).Encode()
	if err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.SetBytes(int64(len(buf)))
	for i := 0; i < b.N; i++ {
		if _, err := DecodeMessage(buf); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkNewRecord(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if _, err := NewRecord("trees", UserIDMin+i%UserIDRange, ProducerAsync); err != nil {
			b.Fatal(err)
		}
	}
}
