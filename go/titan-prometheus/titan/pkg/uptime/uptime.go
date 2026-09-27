/*
 * Uptime Package
 */

package uptime

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

/*
 * init()
 */

var startTime time.Time

/*
 * init()
 */

// init sets a rough start time
func init() {
	startTime = time.Now()
}

/*
 * Public Functions
 */

// Uptime returns the uptime since the start of the application
func Uptime() time.Duration {
	return time.Now().Sub(startTime)
}

func uptimeSeconds() float64 {
	return Uptime().Seconds()
}

func uptimeSecondsSignature() func() float64 {
	return uptimeSeconds
}

//UptimeCounterFunc returns a prometheus.CounterFunc for Uptime
func UptimeCounterFunc(namespace string) prometheus.CounterFunc {
	// alternatively use process_start_time_seconds
	counterOpts := prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "uptime_seconds",
		Help:      "The uptime of the process in seconds.",
	}

	// Note: Generally it should be threadsafe, however, since it's uptime, minor inconsistency should be okay
	return prometheus.NewCounterFunc(counterOpts, uptimeSeconds)
}
