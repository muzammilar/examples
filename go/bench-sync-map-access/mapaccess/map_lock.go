package mapaccess

import "sync"

type LockMap struct {
	mu    sync.Mutex
	items map[string]any
}

func NewLockMap() *LockMap {
	return &LockMap{
		items: make(map[string]any),
	}
}

func (m *LockMap) Get(key string) (any, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	val, ok := m.items[key]
	return val, ok
}

func (m *LockMap) Set(key string, value any) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.items[key] = value
}

func (m *LockMap) Delete(key string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.items, key)
}
