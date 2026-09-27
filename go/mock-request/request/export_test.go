package request

import "time"

// SetClock replaces the clock used by m. It is only compiled into tests.
func SetClock(m *Metrics, now func() time.Time) { m.now = now }
