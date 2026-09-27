// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"fmt"

	"github.com/twmb/franz-go/pkg/kgo"
)

// NewClient creates a franz-go client. A single `kgo.Client` is safe for concurrent use and handles
// producing, consuming and admin requests, so a program generally needs only one.
// Note: creating a client does not connect to kafka, connections are opened lazily on the first request.
func NewClient(brokers []string, clientName string, verbose bool, opts ...kgo.Opt) (*kgo.Client, error) {
	base := []kgo.Opt{
		kgo.SeedBrokers(brokers...),
		kgo.ClientID(fmt.Sprintf("%s-%s", clientName, Hostname())), // the id of the client. It should generally have a hostname as well
		kgo.WithLogger(KgoLogger(verbose)),
	}
	return kgo.NewClient(append(base, opts...)...)
}
