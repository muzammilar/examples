// Load generator and loss check for Redpanda (any Kafka-API broker), using franz-go.
//
// Produces RATE records/s for DURATION with acks=all and the idempotent producer (franz-go's
// defaults), prints one line per second (acked, failed, ack latency, consumed by a group
// consumer), then reads the whole topic back and checks that every acknowledged record is there
// exactly once. Exit status 1 if an acknowledged record is missing or duplicated.
//
// Record value: run id (8 B) | sequence (8 B) | send time ns (8 B) | padding to RECORD_SIZE.
package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"math/rand/v2"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	n, err := strconv.Atoi(env(k, strconv.Itoa(def)))
	if err != nil {
		fail("%s: %v", k, err)
	}
	return n
}

func fail(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "load: "+format+"\n", a...)
	os.Exit(2)
}

func logf(start time.Time, format string, a ...any) {
	fmt.Printf("[%s %6.1fs] %s\n", time.Now().UTC().Format("15:04:05"), time.Since(start).Seconds(), fmt.Sprintf(format, a...))
}

// window collects ack latencies for one reporting interval
type window struct {
	mu   sync.Mutex
	lats []time.Duration
}

func (w *window) add(d time.Duration) { w.mu.Lock(); w.lats = append(w.lats, d); w.mu.Unlock() }
func (w *window) take() []time.Duration {
	w.mu.Lock()
	l := w.lats
	w.lats = nil
	w.mu.Unlock()
	sort.Slice(l, func(i, j int) bool { return l[i] < l[j] })
	return l
}

func pct(l []time.Duration, p float64) time.Duration {
	if len(l) == 0 {
		return 0
	}
	return l[min(len(l)-1, int(float64(len(l))*p))]
}

func ms(d time.Duration) string { return fmt.Sprintf("%.1f", float64(d.Microseconds())/1000) }

func main() {
	brokers := strings.Split(env("BROKERS", "localhost:9092"), ",")
	topic := env("TOPIC", "load")
	partitions := envInt("PARTITIONS", 6)
	replicas := envInt("REPLICAS", 3)
	rate := envInt("RATE", 5000)
	size := max(envInt("RECORD_SIZE", 512), 24)
	duration, err := time.ParseDuration(env("DURATION", "60s"))
	if err != nil {
		fail("DURATION: %v", err)
	}
	metaMinAge, err := time.ParseDuration(env("METADATA_MIN_AGE", "5s"))
	if err != nil {
		fail("METADATA_MIN_AGE: %v", err)
	}
	runID := rand.Uint64()

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	admin, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		fail("client: %v", err)
	}
	adm := kadm.NewClient(admin)
	if r, err := adm.CreateTopic(ctx, int32(partitions), int16(replicas), nil, topic); err != nil && !strings.Contains(err.Error(), "TOPIC_ALREADY_EXISTS") {
		fail("create topic %s: %v (%v)", topic, err, r.Err)
	}

	// acks=all and idempotence are franz-go defaults; retries are unlimited within the
	// delivery timeout, so a record fails only if it cannot be written for DELIVERY_TIMEOUT
	prod, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.DefaultProduceTopic(topic),
		kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.RecordDeliveryTimeout(30*time.Second),
		kgo.ProducerLinger(0),
		// how soon the client may re-fetch metadata after a NOT_LEADER error (franz-go default 5s)
		kgo.MetadataMinAge(metaMinAge),
	)
	if err != nil {
		fail("producer: %v", err)
	}
	group := fmt.Sprintf("load-%x", runID)
	cons, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.ConsumeTopics(topic),
		kgo.ConsumerGroup(group),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtEnd()),
	)
	if err != nil {
		fail("consumer: %v", err)
	}

	var (
		acked, failed, consumed atomic.Int64
		ackedSet                sync.Map // seq -> struct{}
		slow                    atomic.Int64 // acks that took over 1 s
		worstMu                 sync.Mutex
		worst                   time.Duration // slowest ack and when it was sent
		worstSent               time.Time
		firstErr                atomic.Value
		win                     window
		inflight                sync.WaitGroup
	)
	start := time.Now()

	consCtx, consStop := context.WithCancel(context.Background())
	go func() {
		for consCtx.Err() == nil {
			f := cons.PollFetches(consCtx)
			f.EachRecord(func(r *kgo.Record) {
				if len(r.Value) >= 16 && binary.BigEndian.Uint64(r.Value) == runID {
					consumed.Add(1)
				}
			})
		}
	}()

	go func() { // per-second report
		t := time.NewTicker(time.Second)
		defer t.Stop()
		var pa, pf, pc int64
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
			a, f, c := acked.Load(), failed.Load(), consumed.Load()
			l := win.take()
			logf(start, "acked %6d/s  failed %4d  consumed %6d/s  ack p50 %6s p99 %7s max %7s ms", a-pa, f-pf, c-pc,
				ms(pct(l, .5)), ms(pct(l, .99)), ms(pct(l, 1)))
			pa, pf, pc = a, f, c
		}
	}()

	fmt.Printf("load: %d records/s of %d B for %s to %s (%d partitions, RF %d), acks=all, idempotent, metadata min age %s; run %x\n",
		rate, size, duration, topic, partitions, replicas, metaMinAge, runID)
	pad := make([]byte, size-24)
	var seq uint64
	tick := time.NewTicker(10 * time.Millisecond)
	end := time.After(duration)
	perTick := float64(rate) / 100
	var owed float64
loop:
	for {
		select {
		case <-ctx.Done():
			break loop
		case <-end:
			break loop
		case <-tick.C:
		}
		owed += perTick
		for ; owed >= 1; owed-- {
			v := make([]byte, size)
			binary.BigEndian.PutUint64(v[0:], runID)
			binary.BigEndian.PutUint64(v[8:], seq)
			sent := time.Now()
			binary.BigEndian.PutUint64(v[16:], uint64(sent.UnixNano()))
			copy(v[24:], pad)
			s := seq
			seq++
			inflight.Add(1)
			prod.Produce(context.Background(), &kgo.Record{Value: v}, func(r *kgo.Record, err error) {
				defer inflight.Done()
				now := time.Now()
				if err != nil {
					failed.Add(1)
					firstErr.CompareAndSwap(nil, err.Error())
					return
				}
				acked.Add(1)
				ackedSet.Store(s, struct{}{})
				win.add(now.Sub(sent))
				if d := now.Sub(sent); d > time.Second {
					slow.Add(1)
				}
				worstMu.Lock()
				if d := now.Sub(sent); d > worst {
					worst, worstSent = d, sent
				}
				worstMu.Unlock()
			})
		}
	}
	tick.Stop()
	inflight.Wait()
	time.Sleep(2 * time.Second) // let the group consumer catch up for the last report line
	consStop()
	cons.Close()
	prod.Close()
	cancel()

	sent := seq
	a, f := acked.Load(), failed.Load()
	fmt.Printf("\nproduced: sent %d, acked %d, failed %d (after retries)", sent, a, f)
	if e := firstErr.Load(); e != nil {
		fmt.Printf(", first error: %s", e)
	}
	fmt.Printf("\nslowest ack: %s ms (sent at %.1fs); acks slower than 1 s: %d\n", ms(worst), worstSent.Sub(start).Seconds(), slow.Load())

	// read the topic back from the start up to the current end offsets
	vctx, vcancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer vcancel()
	ends, err := adm.ListEndOffsets(vctx, topic)
	if err != nil {
		fail("end offsets: %v", err)
	}
	want := map[int32]int64{}
	ends.Each(func(o kadm.ListedOffset) {
		if o.Offset > 0 {
			want[o.Partition] = o.Offset
		}
	})
	rd, err := kgo.NewClient(kgo.SeedBrokers(brokers...), kgo.ConsumeTopics(topic),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()))
	if err != nil {
		fail("reader: %v", err)
	}
	seen := make(map[uint64]int, a)
	var total int64
	for len(want) > 0 {
		fs := rd.PollFetches(vctx)
		if vctx.Err() != nil {
			fail("read back timed out with %d partitions left", len(want))
		}
		fs.EachError(func(t string, p int32, err error) { fmt.Fprintf(os.Stderr, "read %s/%d: %v\n", t, p, err) })
		fs.EachRecord(func(r *kgo.Record) {
			total++
			if len(r.Value) >= 16 && binary.BigEndian.Uint64(r.Value) == runID {
				seen[binary.BigEndian.Uint64(r.Value[8:])]++
			}
			if w, ok := want[r.Partition]; ok && r.Offset+1 >= w {
				delete(want, r.Partition)
			}
		})
	}
	rd.Close()
	admin.Close()

	var lost, dup, unackedPresent int64
	ackedSet.Range(func(k, _ any) bool {
		if seen[k.(uint64)] == 0 {
			lost++
		}
		return true
	})
	for s, n := range seen {
		if n > 1 {
			dup += int64(n - 1)
		}
		if _, ok := ackedSet.Load(s); !ok {
			unackedPresent++
		}
	}
	fmt.Printf("read back: %d records of this run in the topic (%d in total); acked but missing: %d, duplicates: %d, failed-but-written: %d\n",
		len(seen), total, lost, dup, unackedPresent)
	if lost > 0 || dup > 0 {
		fmt.Println("CHECK FAILED")
		os.Exit(1)
	}
	fmt.Println("CHECK PASSED: every acknowledged record is in the topic exactly once")
}
