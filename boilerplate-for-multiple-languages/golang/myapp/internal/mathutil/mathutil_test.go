package mathutil

import (
	"testing"
)

// TestAdd tests the Add function.
func TestAdd(t *testing.T) {
	result := Add(2, 3)
	expected := 5

	if result != expected {
		t.Errorf("Add(2, 3) = %d; expected %d", result, expected)
	}
}

// TestMultiply tests the Multiply function.
func TestMultiply(t *testing.T) {
	result := Multiply(2, 3)
	expected := 6

	if result != expected {
		t.Errorf("Multiply(2, 3) = %d; expected %d", result, expected)
	}
}

// BenchmarkAdd benchmarks the Add function.
func BenchmarkAdd(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Add(2, 3)
	}
}

// BenchmarkMultiply benchmarks the Multiply function.
func BenchmarkMultiply(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Multiply(2, 3)
	}
}
