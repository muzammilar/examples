# Averaged Gauge Summary

A basic example of a circular ring buffer implementation that stores the `count` of events and the `sum` of the values of all event *per interval*.

This is similar to Prometheus's *summary* metrics except the the `count` the `sum` is *per bucket* and not global/since the start of the application.

Alternatively, this calulation can be performed in a metrics server, like Prometheus using counters.

No locks are needed since the application only has one metrics thread. Morever, the program doesn't try to compute the average over multiple time buckets, but only the latest time bucket.

**Note:** This example only provides an approximation of the metric and usually not the actual average,
since the query/fetch time of the metric may vary due to computing the average over latest bucket (i.e. number of data points used to compute average vary depending on the time when the system is queried for the data).
Due to this reason, this example is ideal for a push-based metrics system but should also work with a pull-based system depending on the frequency of fetching the data.

**Note:** The values passed for averaging cannot be negative. That behaviour is undefined.

**Note:** Guage Values may not be accurate for an empty bucket/missing metrics. The average is `0` and the min value is `math.MaxFloat64` and the max value is `-math.MaxFloat64`

### Build and Test
```sh
docker-compose up --build --detach

```

### Application Help

The application has generator routines and aggregator routine. The generators generate a metric which is sent over a channel to the aggregator. Generators writes are non-blocking on the channel while by the aggregator are always blocking.

```
# application help
Usage of ./guageavg:
  -buckets uint
      Number of buckets to store historical data. (default 3)
  -bucketinterval uint
      The size of the time interval (in seconds) for which the average is computed, i.e. a single bucket is used. (default 15)
  -chansize uint
      The size of the channel used to pass metrics from generators to aggregator. (default 1000)
  -generators uint
      Number of metric generator routines. (default 3)
  -geninterval string
      The time duration, as a string, that a generator waits for between sending new metrics to the aggregator. (default "50ms")
  -genstatsfreq uint
      The number of metrics generated after which a generator prints its own counters (about number of metrics generated and successfully sent). (default 500)
```
