// Copyright 2015 The Prometheus Authors
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// A simple example exposing fictional RPC latencies with different types of
// random distributions (uniform, normal, and exponential) as Prometheus
// metrics.
package main

import (
	"flag"
	"time"

	"github.com/muzammilar/examples-go/titan-prometheus/titan/internal/app/promstats"
	"github.com/muzammilar/examples-go/titan-prometheus/titan/internal/app/titan"
)

var (
	// ldflags
	date   string
	commit string
	// cli flags
	addr              string
	uniformDomain     *float64
	normDomain        *float64
	normMean          *float64
	oscillationPeriod *time.Duration
)

// The example has been taken from the following: https://raw.githubusercontent.com/prometheus/client_golang/master/examples/random/main.go

// ideally flags should be parsed in main and not init, but for sake of example, we'll use `init`
func init() {
	// flags
	// Note: since addr is assigned the value at init time, it will never get the parsed value. It will always get default.
	addr = *flag.String("listen-address", ":8080", "The address to listen on for HTTP requests.")
	uniformDomain = flag.Float64("uniform.domain", 0.0002, "The domain for the uniform distribution.")
	normDomain = flag.Float64("normal.domain", 0.0002, "The domain for the normal distribution.")
	normMean = flag.Float64("normal.mean", 0.00001, "The mean for the normal distribution.")
	oscillationPeriod = flag.Duration("oscillation-period", 10*time.Minute, "The duration of the rate oscillation period.")
	flag.Parse()
}

func main() {
	// custom initializations
	promstats.Init(*normMean, *normDomain)

	// Start a stats and metrics aggregation application

	// Start Titan to generate metrics (has go routines)
	titan.StartTitan(*oscillationPeriod, *uniformDomain, *normDomain, *normMean)

	promstats.PromServer(addr)
}
