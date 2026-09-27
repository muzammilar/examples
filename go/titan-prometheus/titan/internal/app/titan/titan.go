/*
 * Titan Oscillator Metrics Generator
 */

package titan

import (
	"fmt"
	"math"
	"math/rand"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/muzammilar/examples-go/titan-prometheus/titan/internal/app/promstats"
)

/*
 * Constants
 */

/*
 * Public Functions
 */

func StartTitan(oscillationPeriod time.Duration, uniformDomain float64, normDomain float64, normMean float64) {
	start := time.Now()

	oscillationFactor := getOscillationFactorFunc(start, oscillationPeriod)

	// There defer function is not needed and is only used as an example
	defer func() {
		fmt.Println("All Titan Oscillators have been started.")
	}()

	// Prometheus Example: Periodically record some sample latencies for the three services.
	go exponentialOscillator([]string{"randprom", "exponential"}, oscillationFactor)
	go uniformOscillator([]string{"randprom", "uniform"}, oscillationFactor, uniformDomain)
	go normalOscillator([]string{"randprom", "normal"}, oscillationFactor, normDomain, normMean)

	// gokit-metrics prometheus example

	// gokit-metrics statsd example

}

/*
 * Private Functions
 */

func exponentialOscillator(labels []string, oscillationFactor func() float64) {
	for {
		v := rand.ExpFloat64() / 1e6
		promstats.RpcDurations.WithLabelValues(labels...).Observe(v)
		time.Sleep(time.Duration(50*oscillationFactor()) * time.Millisecond)
	}
}

func uniformOscillator(labels []string, oscillationFactor func() float64, uniformDomain float64) {
	v := rand.Float64() * uniformDomain
	promstats.RpcDurations.WithLabelValues(labels...).Observe(v)
	time.Sleep(time.Duration(100*oscillationFactor()) * time.Millisecond)

}

func normalOscillator(labels []string, oscillationFactor func() float64, normDomain float64, normMean float64) {
	for {
		v := (rand.NormFloat64() * normDomain) + normMean
		promstats.RpcDurations.WithLabelValues(labels...).Observe(v)
		// Demonstrate exemplar support with a dummy ID. This
		// would be something like a trace ID in a real
		// application.  Note the necessary type assertion. We
		// already know that rpcDurationsHistogram implements
		// the ExemplarObserver interface and thus don't need to
		// check the outcome of the type assertion.
		promstats.RpcDurationsHistogram.(prometheus.ExemplarObserver).ObserveWithExemplar(
			v, prometheus.Labels{"dummyID": fmt.Sprint(rand.Intn(100000))},
		)
		time.Sleep(time.Duration(75*oscillationFactor()) * time.Millisecond)
	}

}

func getOscillationFactorFunc(start time.Time, oscillationPeriod time.Duration) func() float64 {
	oscillationFactor := func() float64 {
		return 2 + math.Sin(math.Sin(2*math.Pi*float64(time.Since(start))/float64(oscillationPeriod)))
	}
	return oscillationFactor
}
