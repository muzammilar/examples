package mapaccess

import "sync"

// RWLockMap is a struct with a map data structure and a RWMutex.
type RWLockMap struct {
	data map[string]any
	sync.RWMutex
}

// NewRWLockMap creates and returns a new RWLockMap.
func NewRWLockMap() *RWLockMap {
	return &RWLockMap{
		data: make(map[string]any),
	}
}

// Set sets a key-value pair in the map.
func (m *RWLockMap) Set(key string, value any) {
	m.Lock()
	defer m.Unlock()
	m.data[key] = value
}

// Get retrieves a value from the map by key.
func (m *RWLockMap) Get(key string) (any, bool) {
	m.RLock() // Use RLock for read operations
	defer m.RUnlock()
	val, ok := m.data[key]
	return val, ok
}

// Delete removes a key-value pair from the map.
func (m *RWLockMap) Delete(key string) {
	m.Lock()
	defer m.Unlock()
	delete(m.data, key)
}
