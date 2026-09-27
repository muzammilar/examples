// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"

	"github.com/twmb/franz-go/pkg/kgo"
)

// Message is the payload sent by producers and read by consumers
type Message struct {
	Async  int    // Whether the data is sent as sync or async
	UserId int    // This ID is NOT unique between messages and can be repeated
	Data   string // Tree name derived from the user id
}

// NewMessage creates a message for a user id
func NewMessage(userId int, async int) Message {
	return Message{
		Async:  async,
		UserId: userId,
		Data:   Trees[userId%len(Trees)],
	}
}

// Encode serializes the message as JSON
// Note: JSON serialization is noticeably slower than binary formats (like protobufs)
func (m Message) Encode() ([]byte, error) {
	return json.Marshal(m)
}

// DecodeMessage deserializes a JSON message
func DecodeMessage(b []byte) (Message, error) {
	var m Message
	err := json.Unmarshal(b, &m)
	return m, err
}

// NewRecord creates a kafka record for a user id. The user id is the partitioning key, so all the
// messages of a user land on the same partition with the hash partitioner.
func NewRecord(topic string, userId int, async int) (*kgo.Record, error) {
	value, err := NewMessage(userId, async).Encode()
	if err != nil {
		return nil, err
	}
	return &kgo.Record{
		Topic: topic,
		Key:   []byte(strconv.Itoa(userId)),
		Value: value,
	}, nil
}

// SplitList splits a comma separated list, trimming whitespace and dropping empty entries
func SplitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Hostname returns the hostname or `unknown`
func Hostname() string {
	name, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return name
}
