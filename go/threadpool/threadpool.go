package main

import "fmt"

type Message struct {
	ID      int
	Payload string
}

var messageCount = 10000000
var poolSize = 50
var dataBufferSize = 20000

var numWriters = 500

func writers(poolSemaphoreChannel chan *Message, dataStream <-chan *Message) {
	for i := 0; i < numWriters; i++ {
		go func() {
			for message := range dataStream {
				poolSemaphoreChannel <- message
			}
		}()
	}
}

func dataGenerators(dataStream chan *Message) {
	for i := 0; i < numWriters; i++ {
		go func(writerId int) {
			for message := range dataStream {
				dataStream <- message
			}
		}(i)
	}
}

func workers(poolSemaphoreChannel <-chan *Message, result ) {
	for i := 0; i < poolSize; i++ { //not that the poolSize initializes the number of pool workers. It's the same as size of poolSemaphoreChannel
		go func(workerId int) {
			for msg := range poolSemaphoreChannel {
				fmt.Printf("worker %d received message %d\n", workerId, msg.ID)
			}
		}(i)
	}

}

func main() {

	// create channels
	poolSemaphoreChannel := make(chan *Message, poolSize)
	dataStream := make(chan *Message, dataBufferSize)

	dataArr := make([]*Message, messageCount)

	// generate raw data
	for i := 0; i < messageCount; i++ {
		data := &Message{ID: i}
		dataArr[i] = data
	}
	fmt.Printf("Generated Raw Data\n")

}
