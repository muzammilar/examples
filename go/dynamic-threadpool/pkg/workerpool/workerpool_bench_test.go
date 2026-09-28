package workerpool

import (
	"context"
	"fmt"
	"testing"
)

func noop(context.Context) {}

// BenchmarkSubmit measures Submit throughput (including the final drain) for
// pools of several fixed sizes.
func BenchmarkSubmit(b *testing.B) {
	for _, size := range []int{1, 4, 16, 64} {
		b.Run(fmt.Sprintf("workers=%d", size), func(b *testing.B) {
			b.ReportAllocs()
			p, err := New(context.Background(), Config{Min: size, Max: size, QueueSize: 128})
			if err != nil {
				b.Fatal(err)
			}
			ctx := context.Background()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if err := p.Submit(ctx, noop); err != nil {
					b.Fatal(err)
				}
			}
			p.Close()
		})
	}
}

// BenchmarkSubmitParallel measures Submit throughput with many producers.
func BenchmarkSubmitParallel(b *testing.B) {
	for _, size := range []int{4, 16, 64} {
		b.Run(fmt.Sprintf("workers=%d", size), func(b *testing.B) {
			b.ReportAllocs()
			p, err := New(context.Background(), Config{Min: size, Max: size, QueueSize: 128})
			if err != nil {
				b.Fatal(err)
			}
			ctx := context.Background()
			b.ResetTimer()
			b.RunParallel(func(pb *testing.PB) {
				for pb.Next() {
					if err := p.Submit(ctx, noop); err != nil {
						b.Error(err)
						return
					}
				}
			})
			p.Close()
		})
	}
}

// BenchmarkGrowShrink measures the cost of adding n workers and removing them
// again (goroutine start plus stop signal).
func BenchmarkGrowShrink(b *testing.B) {
	for _, n := range []int{1, 16, 128} {
		b.Run(fmt.Sprintf("n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			p, err := New(context.Background(), Config{Min: 1, Max: 1 + n})
			if err != nil {
				b.Fatal(err)
			}
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if err := p.Grow(n); err != nil {
					b.Fatal(err)
				}
				if err := p.Shrink(n); err != nil {
					b.Fatal(err)
				}
			}
			b.StopTimer()
			p.Close()
		})
	}
}
