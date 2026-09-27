// The common package contains the shared code between the admin, producer and consumer binaries

package common

const (
	// Producer Types (Sync/Async)
	ProducerSync  = iota // 0
	ProducerAsync        // 1
)

const (
	// Defaults
	DefaultKafkaBrokers        = "kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092" // The default kafka brokers as a comma separated list (docker network)
	DefaultTopic               = "trees"                                                          // The default topic to publish or subscribe
	DefaultLoggingLevel        = "info"                                                           // The default logging level of the application
	DefaultMetricsAddr         = ":8080"                                                          // The default address to expose prometheus metrics on
	DefaultConnectionBackoffMs = 5000                                                             // The default time in milliseconds before retrying to connect to kafka
	// Defaults - Admin only defaults
	DefaultPartitions        = 13 // The default number of partitions for a created topic
	DefaultReplicationFactor = 3  // The default replication factor for a created topic
	// Defaults - Producer only defaults
	DefaultPartitioner           = PartitionHash // The default partitioner for producers
	DefaultMessageSendIntervalMs = 100           // The default interval between sending messages for producers
	// Defaults - Consumer only defaults
	DefaultConsumerGroup = "treeconsumer"      // The default consumer group for querying kafka by consumers
	DefaultBalancer      = BalancerCooperative // The default consumer group balancer for consumers
)

const (
	// User ID range
	UserIDMax = 5000 // The max possible value of user ID (not inclusive)
	UserIDMin = 50   // The min possible value of user ID (inclusive)
)

var UserIDRange = UserIDMax - UserIDMin

const (
	// Partitioners
	PartitionHash       = "hash"       // compute the hash of the message key and select the partition (murmur2, java client compatible)
	PartitionRand       = "rand"       // select a random partition (sticky per batch)
	PartitionRoundRobin = "roundrobin" // select partition using round robin i.e. (i+1)%n
)

// Supported partitioners
var SupportedPartitioners = []string{PartitionHash, PartitionRand, PartitionRoundRobin}

const (
	// Consumer group balancers
	BalancerRange       = "range"
	BalancerRoundRobin  = "roundrobin"
	BalancerSticky      = "sticky"
	BalancerCooperative = "cooperative-sticky" // incremental rebalancing (partitions are not all revoked on rebalance)
)

// Supported balancers
var SupportedBalancers = []string{BalancerRange, BalancerRoundRobin, BalancerSticky, BalancerCooperative}

// Tree Names
var Trees = []string{
	"American Beech",
	"American Chestnut",
	"American Elm",
	"American Hophornbeam",
	"American Hornbeam",
	"American Larch",
	"Arborvitae",
	"Balsam Fir",
	"Basswood",
	"Bigtooth Aspen",
	"Bitternut Hickory",
	"Black Ash",
	"Black Birch",
	"Black Cherry",
	"Black Locust",
	"Black Oak",
	"Black Walnut",
	"Black Willow",
	"Butternut",
	"Chestnut Oak",
	"Cucumber Tree",
	"Eastern Cottonwood",
	"Eastern Hemlock",
	"Eastern Redcedar",
	"Eastern White Pine",
	"Gray Birch",
	"Hawthorn",
	"Honey-Locust",
	"Northern Red Oak",
	"Paper Birch",
	"Pignut Hickory",
	"Pin Cherry",
	"Pitch Pine",
	"Quaking Aspen",
	"Red Maple",
	"Red Pine",
	"Red Spruce",
	"Sassafras",
	"Scarlet Oak",
	"Shadbush",
	"Shagbark Hickory",
	"Silver Maple",
	"Slippery Elm",
	"Sugar Maple",
	"Sycamore",
	"The Maples",
	"The Oaks",
	"Tulip Tree",
	"White Ash",
	"White Oak",
	"White Spruce",
	"Yellow Birch",
}
