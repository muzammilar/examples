package mapaccess

import "sync"

// SyncMap is a struct with a sync.Map data structure.
type SyncMap struct {
	data sync.Map
}

// NewSyncMap creates and returns a new SyncMap.
func NewSyncMap() *SyncMap {
	return &SyncMap{}
}

// Set sets a key-value pair in the map.
func (m *SyncMap) Set(key string, value any) {
	m.data.Store(key, value)
}

// Get retrieves a value from the map by key.
func (m *SyncMap) Get(key string) (any, bool) {
	return m.data.Load(key)
}

// Delete removes a key-value pair from the map.
func (m *SyncMap) Delete(key string) {
	m.data.Delete(key)
}
