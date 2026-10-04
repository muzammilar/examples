// The module trees/bench is a benchmark for any Kafka-API cluster (Apache Kafka, Redpanda, ...)

package main

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"math/rand/v2"
	"slices"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kerr"
	"github.com/twmb/franz-go/pkg/kgo"
)

// header carrying the send time in ns (the record timestamp has millisecond resolution)
const sendHeader = "send-ns"

// --- latency samples -------------------------------------------------------------------------

type lats struct {
	mu sync.Mutex
	d  []time.Duration
}

func (l *lats) add(d time.Duration) { l.mu.Lock(); l.d = append(l.d, d); l.mu.Unlock() }

// Percentiles in milliseconds
type Percentiles struct {
	N    int     `json:"n"`
	P50  float64 `json:"p50"`
	P99  float64 `json:"p99"`
	P999 float64 `json:"p99_9"`
	Max  float64 `json:"max"`
}

func (l *lats) percentiles() Percentiles {
	l.mu.Lock()
	defer l.mu.Unlock()
	if len(l.d) == 0 {
		return Percentiles{}
	}
	slices.Sort(l.d)
	at := func(q float64) float64 {
		return float64(l.d[min(len(l.d)-1, int(float64(len(l.d))*q))].Microseconds()) / 1000
	}
	return Percentiles{N: len(l.d), P50: at(.5), P99: at(.99), P999: at(.999), Max: at(1)}
}

func (p Percentiles) String() string {
	return fmt.Sprintf("p50 %.2f  p99 %.2f  p99.9 %.2f  max %.2f ms (n=%d)", p.P50, p.P99, p.P999, p.Max, p.N)
}

// --- acknowledged records --------------------------------------------------------------------

// tally is what the producer got acknowledged: count, checksum (sum of CRC32 of the values), sequences
type tally struct {
	mu   sync.Mutex
	n    int64
	sum  uint64
	seqs map[uint64]struct{}
}

func newTally() *tally { return &tally{seqs: map[uint64]struct{}{}} }

func (t *tally) add(seq uint64, v []byte) {
	t.mu.Lock()
	t.n++
	t.sum += uint64(crc32.ChecksumIEEE(v))
	t.seqs[seq] = struct{}{}
	t.mu.Unlock()
}

// --- records ---------------------------------------------------------------------------------

type bench struct {
	c       *config
	runID   uint64
	payload []byte
	adm     *kadm.Client
	hooks   []kgo.Opt // metrics
}

func (b *bench) record(topic string, seq uint64) *kgo.Record {
	v := make([]byte, b.c.Size)
	binary.BigEndian.PutUint64(v[0:], b.runID)
	binary.BigEndian.PutUint64(v[8:], seq)
	copy(v[minSize:], b.payload)
	now := time.Now()
	ns := make([]byte, 8)
	binary.BigEndian.PutUint64(ns, uint64(now.UnixNano()))
	return &kgo.Record{
		Topic:     topic,
		Key:       []byte(strconv.FormatUint(seq*2654435761%10000, 10)), // 10k distinct keys
		Value:     v,
		Timestamp: now,
		Headers:   []kgo.RecordHeader{{Key: sendHeader, Value: ns}},
	}
}

// parse returns the sequence and send time of a record of this run
func (b *bench) parse(r *kgo.Record) (ok bool, seq uint64, sent time.Time) {
	if len(r.Value) < minSize || binary.BigEndian.Uint64(r.Value) != b.runID {
		return false, 0, time.Time{}
	}
	sent = r.Timestamp
	for _, h := range r.Headers {
		if h.Key == sendHeader && len(h.Value) == 8 {
			sent = time.Unix(0, int64(binary.BigEndian.Uint64(h.Value)))
		}
	}
	return true, binary.BigEndian.Uint64(r.Value[8:]), sent
}

// --- clients ---------------------------------------------------------------------------------

func (b *bench) client(opts ...kgo.Opt) *kgo.Client {
	base := append([]kgo.Opt{kgo.SeedBrokers(b.c.Brokers...)}, b.hooks...)
	cl, err := kgo.NewClient(append(base, opts...)...)
	if err != nil {
		panic(err) // only invalid options fail here
	}
	return cl
}

// acks=all and the idempotent producer (franz-go's defaults, set explicitly); with idempotence
// franz-go keeps up to 5 produce requests in flight per broker
func (b *bench) producer(opts ...kgo.Opt) *kgo.Client {
	return b.client(append([]kgo.Opt{
		kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.RecordDeliveryTimeout(60 * time.Second),
		kgo.ProducerLinger(b.c.Linger),
		kgo.ProducerBatchCompression(b.c.Compression),
		kgo.ProducerBatchMaxBytes(1 << 20),
		kgo.MaxBufferedRecords(50000),
	}, opts...)...)
}

// a direct consumer of every partition of topic (no group), from the start or the current end
func (b *bench) consumer(topic string, fromStart bool, opts ...kgo.Opt) *kgo.Client {
	off := kgo.NewOffset().AtEnd()
	if fromStart {
		off = kgo.NewOffset().AtStart()
	}
	ps := map[int32]kgo.Offset{}
	for p := range b.c.Partitions {
		ps[p] = off
	}
	return b.client(append([]kgo.Opt{
		kgo.ConsumePartitions(map[string]map[int32]kgo.Offset{topic: ps}),
		kgo.FetchMaxWait(500 * time.Millisecond),
	}, opts...)...)
}

// recreateTopic deletes the topic (if it exists) and creates it again; deletion is asynchronous
func (b *bench) recreateTopic(ctx context.Context, topic string) error {
	_, _ = b.adm.DeleteTopics(ctx, topic)
	var last error
	for range 60 {
		r, err := b.adm.CreateTopic(ctx, b.c.Partitions, b.c.Replication, b.c.TopicConfigs, topic)
		if err == nil && r.Err == nil {
			return b.warmTopic(ctx, topic)
		}
		last = errors.Join(err, r.Err)
		if r.Err != nil && !errors.Is(r.Err, kerr.TopicAlreadyExists) {
			return fmt.Errorf("create %s: %w (%s)", topic, r.Err, r.ErrMessage)
		}
		time.Sleep(500 * time.Millisecond)
	}
	return fmt.Errorf("create %s: %w", topic, last)
}

// warmTopic waits until every partition has a leader, then writes one record (not part of the
// run: no run id) to each partition, so leader elections and connections are not timed
func (b *bench) warmTopic(ctx context.Context, topic string) error {
	deadline := time.Now().Add(2 * time.Minute)
	for {
		td, err := b.adm.ListTopics(ctx, topic)
		ready := err == nil && td[topic].Err == nil && len(td[topic].Partitions) == int(b.c.Partitions)
		if ready {
			for _, p := range td[topic].Partitions {
				ready = ready && p.Err == nil && p.Leader >= 0
			}
		}
		if ready {
			break
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("partitions of %s have no leader after 2 minutes", topic)
		}
		time.Sleep(200 * time.Millisecond)
	}
	p := b.producer(kgo.RecordPartitioner(kgo.ManualPartitioner()))
	defer p.Close()
	var recs []*kgo.Record
	for i := range b.c.Partitions {
		recs = append(recs, &kgo.Record{Topic: topic, Partition: i, Value: []byte("warm-up")})
	}
	return p.ProduceSync(ctx, recs...).FirstErr()
}

// --- the run ---------------------------------------------------------------------------------

// Result is the one-line JSON summary
type Result struct {
	Label        string      `json:"label"`
	Brokers      string      `json:"brokers"`
	Mode         string      `json:"mode"` // "rate" or "max"
	TargetRate   int         `json:"target_rate"`
	DurationS    float64     `json:"duration_s"`
	Size         int         `json:"size"`
	Partitions   int32       `json:"partitions"`
	Replication  int16       `json:"replication"`
	Acked        int64       `json:"acked"`
	Failed       int64       `json:"failed"`
	AckedPerS    float64     `json:"acked_per_s"`
	MiBPerS      float64     `json:"mib_per_s"`
	ConsumedLive int64       `json:"consumed_live"`
	Ack          Percentiles `json:"ack_ms"`
	E2E          Percentiles `json:"e2e_ms"`
	Read         int64       `json:"read_back"`
	Missing      int64       `json:"missing"`
	Duplicates   int64       `json:"duplicates"`
	ChecksumOK   bool        `json:"checksum_ok"`
	Txn          *TxnResult  `json:"txn,omitempty"`
	OK           bool        `json:"ok"`
}

func (b *bench) run(ctx context.Context) (*Result, error) {
	c := b.c
	res := &Result{Label: c.Label, TargetRate: c.Rate, Size: c.Size, Partitions: c.Partitions, Replication: c.Replication, Mode: "rate"}
	if c.Rate == 0 {
		res.Mode = "max"
	}
	if err := b.recreateTopic(ctx, c.Topic); err != nil {
		return nil, err
	}

	// live consumer: end-to-end latency = consume time - send time (same host clock)
	var ack, e2e lats
	var warmNs atomic.Int64
	warmNs.Store(1 << 62)
	var live atomic.Int64
	cons := b.consumer(c.Topic, false)
	cctx, cstop := context.WithCancel(ctx)
	cdone := make(chan struct{})
	go func() {
		defer close(cdone)
		for cctx.Err() == nil {
			fs := cons.PollFetches(cctx)
			now := time.Now()
			fs.EachRecord(func(r *kgo.Record) {
				if ok, _, sent := b.parse(r); ok {
					live.Add(1)
					if sent.UnixNano() >= warmNs.Load() {
						e2e.add(now.Sub(sent))
					}
				}
			})
		}
	}()
	// the consumer resolves "end" on its first fetch; give it a moment before producing
	time.Sleep(1500 * time.Millisecond)

	prod := b.producer()
	t := newTally()
	var failed atomic.Int64
	var wg sync.WaitGroup
	var seq uint64
	send := func() {
		r := b.record(c.Topic, seq)
		s := seq
		seq++
		sent := r.Timestamp
		wg.Add(1)
		prod.Produce(ctx, r, func(r *kgo.Record, err error) { // blocks while the buffer is full
			defer wg.Done()
			if err != nil {
				failed.Add(1)
				return
			}
			if sent.UnixNano() >= warmNs.Load() {
				ack.add(time.Since(sent))
			}
			t.add(s, r.Value)
		})
	}
	start := time.Now()
	warmNs.Store(start.Add(c.Warmup).UnixNano())
	end := start.Add(c.Duration)
	if c.Rate == 0 {
		for time.Now().Before(end) && ctx.Err() == nil {
			send()
		}
	} else {
		// pace by elapsed time (a 1 ms ticker drops ticks): send until rate*elapsed records are out
		tick := time.NewTicker(time.Millisecond)
		for now := range tick.C {
			if now.After(end) || ctx.Err() != nil {
				break
			}
			for due := uint64(now.Sub(start).Seconds() * float64(c.Rate)); seq < due; {
				send()
			}
		}
		tick.Stop()
	}
	wg.Wait()
	el := time.Since(start)
	time.Sleep(2 * time.Second) // let the live consumer catch up
	cstop()
	<-cdone
	cons.Close()
	prod.Close()

	res.DurationS = el.Seconds()
	res.Acked, res.Failed, res.ConsumedLive = t.n, failed.Load(), live.Load()
	res.AckedPerS = float64(t.n) / el.Seconds()
	res.MiBPerS = res.AckedPerS * float64(c.Size) / (1 << 20)
	res.Ack, res.E2E = ack.percentiles(), e2e.percentiles()

	// read everything back from offset 0: every acked record exactly once, same checksum
	n, sum, seqs := b.readBack(ctx, c.Topic, t.n)
	res.Read = n
	for s := range t.seqs {
		if seqs[s] == 0 {
			res.Missing++
		}
	}
	for _, k := range seqs {
		res.Duplicates += int64(max(k-1, 0))
	}
	res.ChecksumOK = sum == t.sum && n == t.n
	res.OK = res.Missing == 0 && res.Duplicates == 0 && res.ChecksumOK
	return res, nil
}

// readBack reads every record of this run in topic from offset 0, until it has at least want
// records and nothing new arrived for 3 s (or 5 minutes passed)
func (b *bench) readBack(ctx context.Context, topic string, want int64, opts ...kgo.Opt) (n int64, sum uint64, seqs map[uint64]int) {
	cl := b.consumer(topic, true, opts...)
	defer cl.Close()
	seqs = map[uint64]int{}
	deadline := time.Now().Add(5 * time.Minute)
	idle := time.Now()
	for time.Now().Before(deadline) && ctx.Err() == nil {
		pctx, cancel := context.WithTimeout(ctx, time.Second)
		fs := cl.PollFetches(pctx)
		cancel()
		got := 0
		fs.EachRecord(func(r *kgo.Record) {
			if ok, s, _ := b.parse(r); ok {
				got++
				n++
				sum += uint64(crc32.ChecksumIEEE(r.Value))
				seqs[s]++
			}
		})
		if got > 0 {
			idle = time.Now()
		}
		if n >= want && time.Since(idle) > 3*time.Second {
			break
		}
	}
	return
}

// --- transactions ----------------------------------------------------------------------------

// TxnResult is the exactly-once check
type TxnResult struct {
	Committed       int64       `json:"committed"`
	Aborted         int64       `json:"aborted"`
	Commit          Percentiles `json:"begin_to_commit_ms"`
	ReadCommitted   int64       `json:"read_committed"`
	ReadUncommitted int64       `json:"read_uncommitted"`
	ChecksumOK      bool        `json:"checksum_ok"`
	OK              bool        `json:"ok"`
}

// txns runs c.Txns transactions of c.TxnRecords records on <topic>-txn, every 5th aborted;
// read_committed must return exactly the committed records (count and checksum)
func (b *bench) txns(ctx context.Context) (*TxnResult, error) {
	topic := b.c.Topic + "-txn"
	if err := b.recreateTopic(ctx, topic); err != nil {
		return nil, err
	}
	p := b.producer(kgo.TransactionalID(fmt.Sprintf("bench-%x", b.runID)))
	defer p.Close()
	res := &TxnResult{}
	var commit lats
	var commitSum uint64
	var seq uint64
	for i := range b.c.Txns {
		t0 := time.Now()
		if err := p.BeginTransaction(); err != nil {
			return nil, fmt.Errorf("begin: %w", err)
		}
		var mu sync.Mutex
		var sum uint64
		var perr error
		for range b.c.TxnRecords {
			p.Produce(ctx, b.record(topic, seq), func(r *kgo.Record, err error) {
				mu.Lock()
				defer mu.Unlock()
				if err != nil {
					perr = errors.Join(perr, err)
					return
				}
				sum += uint64(crc32.ChecksumIEEE(r.Value))
			})
			seq++
		}
		if err := p.Flush(ctx); err != nil {
			return nil, fmt.Errorf("flush: %w", err)
		}
		how := kgo.TryCommit
		if i%5 == 4 || perr != nil {
			how = kgo.TryAbort
		}
		if err := p.EndTransaction(ctx, how); err != nil {
			return nil, fmt.Errorf("end transaction: %w", err)
		}
		if how == kgo.TryCommit {
			res.Committed += int64(b.c.TxnRecords)
			commitSum += sum
			commit.add(time.Since(t0))
		} else {
			res.Aborted += int64(b.c.TxnRecords)
		}
	}
	res.Commit = commit.percentiles()
	n, sum, _ := b.readBack(ctx, topic, res.Committed, kgo.FetchIsolationLevel(kgo.ReadCommitted()))
	res.ReadCommitted, res.ChecksumOK = n, sum == commitSum
	res.ReadUncommitted, _, _ = b.readBack(ctx, topic, res.Committed+res.Aborted, kgo.FetchIsolationLevel(kgo.ReadUncommitted()))
	res.OK = res.ReadCommitted == res.Committed && res.ChecksumOK && res.ReadUncommitted == res.Committed+res.Aborted
	return res, nil
}

func newPayload(n int) []byte {
	p := make([]byte, n)
	for i := range p {
		p[i] = byte('a' + rand.IntN(26))
	}
	return p
}
