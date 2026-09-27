// Command threadpool demonstrates a static (fixed-size) worker pool.
//
// Several producer goroutines generate messages and submit them to a pool
// with a fixed number of workers. Each worker "processes" a message and
// sends a result on a results channel, which main aggregates.
package main

import (
	"context"
	"flag"
	"fmt"
	"hash/fnv"
	"log"
	"sync"
	"time"
)

// Message is the unit of data flowing through the example.
type Message struct {
	ID      int
	Payload string
}

// Result is what a worker produces for a Message.
type Result struct {
	MessageID int
	WorkerID  int
	Checksum  uint32
}

// process is the (cheap, CPU-bound) work done for each message.
func process(msg *Message) uint32 {
	h := fnv.New32a()
	h.Write([]byte(msg.Payload))
	return h.Sum32()
}

// produce splits message IDs [0, count) across numProducers goroutines and
// submits one task per message to the pool. It returns when all messages
// have been submitted (or submission failed).
func produce(ctx context.Context, pool *Pool, count, numProducers int, results chan<- Result) error {
	var wg sync.WaitGroup
	errs := make(chan error, numProducers)
	for p := 0; p < numProducers; p++ {
		wg.Add(1)
		go func(producerID int) {
			defer wg.Done()
			for id := producerID; id < count; id += numProducers {
				msg := &Message{ID: id, Payload: fmt.Sprintf("message-%d", id)}
				err := pool.Submit(ctx, func(workerID int) {
					results <- Result{MessageID: msg.ID, WorkerID: workerID, Checksum: process(msg)}
				})
				if err != nil {
					errs <- fmt.Errorf("producer %d: %w", producerID, err)
					return
				}
			}
		}(p)
	}
	wg.Wait()
	close(errs)
	return <-errs // nil if no producer failed
}

func main() {
	workers := flag.Int("workers", 8, "number of fixed pool workers")
	queue := flag.Int("queue", 64, "size of the pool's task queue")
	producers := flag.Int("producers", 4, "number of producer goroutines")
	messages := flag.Int("messages", 100000, "number of messages to process")
	flag.Parse()

	if *workers < 1 || *producers < 1 || *queue < 0 || *messages < 0 {
		log.Fatal("workers and producers must be >= 1; queue and messages must be >= 0")
	}

	start := time.Now()
	pool := New(*workers, *queue)
	results := make(chan Result, *queue)

	// Aggregate results concurrently so workers never block for long.
	perWorker := make([]int, *workers)
	var xor uint32
	done := make(chan struct{})
	go func() {
		defer close(done)
		for r := range results {
			perWorker[r.WorkerID]++
			xor ^= r.Checksum
		}
	}()

	if err := produce(context.Background(), pool, *messages, *producers, results); err != nil {
		log.Printf("submission stopped early: %v", err)
	}
	pool.Close()   // drain queued tasks and wait for workers to exit
	close(results) // safe: no worker can send any more
	<-done

	total := 0
	for id, n := range perWorker {
		fmt.Printf("worker %2d processed %d messages\n", id, n)
		total += n
	}
	fmt.Printf("processed %d/%d messages with %d workers in %s (checksum xor %08x)\n",
		total, *messages, *workers, time.Since(start).Round(time.Millisecond), xor)
}
