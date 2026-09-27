package mapaccess

import (
	"strconv"
	"sync/atomic"
	"testing"
)

func BenchmarkRWLockMap_Set(b *testing.B) {
	m := NewRWLockMap()
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			m.Set("key-"+strconv.Itoa(i), i)
			i++
		}
	})
}

func BenchmarkRWLockMap_Get(b *testing.B) {
	m := NewRWLockMap()
	numItems := 10000
	for i := 0; i < numItems; i++ {
		m.Set("key-"+strconv.Itoa(i), i)
	}

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			m.Get("key-" + strconv.Itoa(i%numItems))
			i++
		}
	})
}

func BenchmarkRWLockMap_Delete(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		m := NewRWLockMap()
		var keys []string
		for i := 0; i < b.N; i++ {
			key := "key-" + strconv.Itoa(i)
			m.Set(key, i)
			keys = append(keys, key)
		}

		b.ResetTimer()
		index := 0
		for pb.Next() {
			m.Delete(keys[index%len(keys)])
			index++
		}
	})
}

func BenchmarkRWLockMap_SetGet(b *testing.B) {
	m := NewRWLockMap()
	var i int64 = -1 // Since the AddInt64 returns the integer after increment, we start at -1 instead of 0

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			key := "key-" + strconv.Itoa(int(atomic.LoadInt64(&i)))
			atomic.AddInt64(&i, 1)

			m.Set(key, i)
			m.Get(key)
		}
	})
}
func BenchmarkRWLockMap_GetMixed(b *testing.B) {
	m := NewRWLockMap()
	numExistingItems := 10000
	for i := 0; i < numExistingItems; i++ {
		m.Set("existing-key-"+strconv.Itoa(i), i)
	}

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			if i%2 == 0 {
				// Get an existing key
				m.Get("existing-key-" + strconv.Itoa(i%numExistingItems))
			} else {
				// Get a non-existing key
				m.Get("non-existing-key-" + strconv.Itoa(i))
			}
			i++
		}
	})
}

func BenchmarkRWLockMap_GetSetMixed(b *testing.B) {
	m := NewRWLockMap()
	numExistingItems := 10000
	for i := 0; i < numExistingItems; i++ {
		m.Set("existing-key-"+strconv.Itoa(i), i)
	}

	var i int64 = -1 // Since the AddInt64 returns the integer after increment, we start at -1 instead of 0

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			idx := int(atomic.AddInt64(&i, 1))

			if idx%2 == 0 {
				// Get an existing key
				m.Get("existing-key-" + strconv.Itoa(idx%numExistingItems))
			} else {
				// Set a new key
				key := "new-key-" + strconv.Itoa(idx)
				val := "new-val-" + strconv.Itoa(idx)
				m.Set(key, val)
			}
		}
	})
}
