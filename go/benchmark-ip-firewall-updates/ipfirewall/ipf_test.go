package ipfirewall

import (
	"context"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

/****************/
/*     Tests    */
/****************/

func TestNewIPFirewallIsActive(t *testing.T) {
	i := NewIPFirewall()
	if i.IsActive() {
		t.Fatalf("TestNewIPFirewallIsActive: The default state of the firewall must be `%s`. Found `%s`.", disabledStr, i.mode)
	}
}

func TestVersionIncrements(t *testing.T) {
	ip := NewIPFirewall()
	var versionIncrements uint64 = 1000
	// incremement versions
	var i uint64
	for i = 0; i < versionIncrements; i++ {
		ip.IncVersion()
	}
	// test increments
	if ip.versionNumber.Load() != versionIncrements {
		t.Fatalf("TestVersionIncrements: Unexpected number of version increments. Expected `%d`. Found `%d`.", versionIncrements, ip.versionNumber.Load())
	}
}

/****************/
/*  Benchmarks  */
/****************/

// Function Benchmarks

func BenchmarkReadVersion(b *testing.B) {

	ip := NewIPFirewall()
	ip.versionNumber.Store(uint64(time.Now().Unix()))

	for i := 0; i < b.N; i++ {
		ip.ReadVersion()
	}
}

func BenchmarkReadVersionAndUpdate(b *testing.B) {

	ip := NewIPFirewall()
	ip.versionNumber.Store(uint64(time.Now().Unix()))
	var ipv uint64
	for i := 0; i < b.N; i++ {
		ipv = ip.ReadVersion()
		ipv += 1 // extra operation
	}
}

func BenchmarkParallelReadVersion(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		ip := NewIPFirewall()
		ip.versionNumber.Store(uint64(time.Now().Unix()))
		for pb.Next() {
			ip.ReadVersion()
		}
	})
}

func BenchmarkParallelReadVersionAndUpdate(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		ip := NewIPFirewall()
		ip.versionNumber.Store(uint64(time.Now().Unix()))
		var ipv uint64
		for pb.Next() {
			ipv = ip.ReadVersion()
			ipv += 1 // extra operation
		}
	})
}

func BenchmarkReadEventuallyConsistentVersion(b *testing.B) {

	ip := NewIPFirewall()
	ip.versionNumber.Store(uint64(time.Now().Unix()))

	for i := 0; i < b.N; i++ {
		ip.ReadEventuallyConsistentVersion()
	}
}

func BenchmarkReadEventuallyConsistentVersionAndUpdate(b *testing.B) {

	ip := NewIPFirewall()
	ip.versionNumber.Store(uint64(time.Now().Unix()))
	var ipv uint64

	for i := 0; i < b.N; i++ {
		ipv = ip.ReadEventuallyConsistentVersion()
		ipv += 1 // extra operation
	}
}

func BenchmarkParallelReadEventuallyConsistentVersion(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		ip := NewIPFirewall()
		ip.versionNumber.Store(uint64(time.Now().Unix()))
		for pb.Next() {
			ip.ReadEventuallyConsistentVersion()
		}
	})
}

func BenchmarkParallelReadEventuallyConsistentVersionAndUpdate(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		ip := NewIPFirewall()
		ip.versionNumber.Store(uint64(time.Now().Unix()))
		for pb.Next() {
			ipv = ip.ReadEventuallyConsistentVersion()
			ipv += 1 // extra operation
		}
	})
}

// Integer Benchmarks
func BenchmarkParallelUint64Struct(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		i := &atomic.Uint64{}
		i.Store(uint64(time.Now().Unix()))
		for pb.Next() {
			i.Load()
		}
	})
}

func BenchmarkParallelUint64StructAndUpdate(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		i := &atomic.Uint64{}
		i.Store(uint64(time.Now().Unix()))
		for pb.Next() {
			ipv = i.Load()
			ipv += 1
		}
	})
}

func BenchmarkParallelUint64Function(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		i := uint64(time.Now().Unix())
		for pb.Next() {
			atomic.LoadUint64(&i)
		}
	})
}

func BenchmarkParallelUint64FunctionAndUpdate(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		i := uint64(time.Now().Unix())
		for pb.Next() {
			ipv = atomic.LoadUint64(&i)
			ipv += 1
		}
	})
}

// Pointer Benchmarks
func BenchmarkIPListPointer(b *testing.B) {
	_, ipnet, _ := net.ParseCIDR("2001:db8::/32")
	ipnetptr := &atomic.Pointer[net.IPNet]{}
	ipnetptr.Store(ipnet)
	for i := 0; i < b.N; i++ {
		ipnetptr.Load()
	}
}

func BenchmarkIPListPointerAndUpdate(b *testing.B) {
	_, ipnet, _ := net.ParseCIDR("2001:db8::/32")
	ipnetptr := &atomic.Pointer[net.IPNet]{}
	ipnetptr.Store(ipnet)
	var ipv uint64
	for i := 0; i < b.N; i++ {
		ipnetptr.Store(ipnetptr.Load())
		ipv += 1
	}
}

func BenchmarkParallelIPListPointer(b *testing.B) {
	_, ipnet, _ := net.ParseCIDR("2001:db8::/32")
	b.RunParallel(func(pb *testing.PB) {
		ipnetptr := &atomic.Pointer[net.IPNet]{}
		ipnetptr.Store(ipnet)
		for pb.Next() {
			ipnetptr.Load()
		}
	})
}

func BenchmarkParallelIPListPointerAndUpdate(b *testing.B) {
	_, ipnet, _ := net.ParseCIDR("2001:db8::/32")
	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		ipnetptr := &atomic.Pointer[net.IPNet]{}
		ipnetptr.Store(ipnet)
		for pb.Next() {
			ipnetptr.Store(ipnetptr.Load())
			ipv += 1
		}
	})
}

// Context Benchmarks
func BenchmarkContext(b *testing.B) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	for i := 0; i < b.N; i++ {
		select {
		case <-ctx.Done():
			// Do Nothing for now
		default:
			// Do Nothing for now
		}
	}
}

func BenchmarkContextAndUpdate(b *testing.B) {

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var ipv uint64

	for i := 0; i < b.N; i++ {
		select {
		case <-ctx.Done():
			ipv += 1
		default:
			ipv -= 1
		}
	}
}

func BenchmarkParallelContext(b *testing.B) {

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			select {
			case <-ctx.Done():
				// Do Nothing for now
			default:
				// Do Nothing for now
			}
		}
	})
}

func BenchmarkParallelContextAndUpdate(b *testing.B) {

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		for pb.Next() {
			select {
			case <-ctx.Done():
				ipv += 1
			default:
				ipv -= 1
			}
		}
	})
}

// RW Mutex Benchmarks
func BenchmarkRWMutex(b *testing.B) {
	lck := &sync.RWMutex{}
	var ipv uint64
	for i := 0; i < b.N; i++ {
		lck.RLock()
		ipv += 1
		lck.RUnlock()
	}
}

func BenchmarkParallelRWMutex(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		lck := &sync.RWMutex{}
		for pb.Next() {
			lck.RLock()
			ipv += 1
			lck.RUnlock()
		}
	})
}

// Mutex Benchmarks
func BenchmarkMutex(b *testing.B) {
	lck := &sync.Mutex{}
	var ipv uint64
	for i := 0; i < b.N; i++ {
		lck.Lock()
		ipv += 1
		lck.Unlock()
	}
}

func BenchmarkParallelMutex(b *testing.B) {

	b.RunParallel(func(pb *testing.PB) {
		var ipv uint64
		lck := &sync.Mutex{}
		for pb.Next() {
			lck.Lock()
			ipv += 1
			lck.Unlock()
		}
	})
}
