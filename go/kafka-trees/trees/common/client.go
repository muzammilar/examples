// The common package contains the shared code between both producer and consumer

package common

import (
	"github.com/Shopify/sarama"
	"github.com/sirupsen/logrus"
)

//ValidateTopicInformation validates that the topic exists and is writeable
func ValidateTopicInformation(brokers []string, topic string, config *sarama.Config, logger *logrus.Logger) bool {
	// create a client
	var client sarama.Client // if we reuse the client, we can probably force refresh metadata as well
	var err error
	client, err = sarama.NewClient(brokers, config)
	if err != nil {
		logger.Errorf("Failed to create a kafka client for '%+v': %#v", brokers, err)
		return false
	}
	// close the client
	defer client.Close()

	// check topic until the topic is created
	var brokerTopics []string
	brokerTopics, err = client.Topics()
	if err != nil {
		logger.Errorf("Kafka client failed to get topics from '%+v': %#v", brokers, err)
		return false
	}
	logger.Infof("Topics for kafka broker '%+v': %#v", brokers, brokerTopics)

	// check if our topic exists in the list
	if !StringInSlice(topic, brokerTopics) {
		logger.Errorf("Failed to find the topic '%s' in kafka topics from '%+v': %#v", topic, brokers, brokerTopics)
		return false
	}

	//check if all the partitions are writeable partitinions (extended safety check)
	var partitions, writeablePartitions []int32
	// get all paritions
	partitions, err = client.Partitions(topic)
	if err != nil {
		logger.Errorf("Kafka client failed to get partitions from '%+v': %#v", brokers, err)
		return false
	}
	logger.Infof("Partitions for kafka topic '%s' from the broker '%+v': %#v", topic, brokers, partitions)
	// get all writeable paritions
	writeablePartitions, err = client.WritablePartitions(topic)
	if err != nil {
		logger.Errorf("Kafka client failed to get writeable partitions from '%+v': %#v", brokers, err)
		return false
	}
	logger.Infof("Writeable Partitions for kafka topic '%s' from the broker '%+v': %#v", topic, brokers, writeablePartitions)
	// check if both are equal. since the lists are sorted, and that partitions are numbered 1...n, it can be done in O(1) by checking the length, the first and the last element.
	if !Int32SliceEquals(partitions, writeablePartitions) {
		logger.Errorf("Not all parititions for topic '%s' seem writeable from '%+v'. Partitons: %#v. Writeable Partitions", topic, brokers, partitions, writeablePartitions)
		return false
	}
	logger.Infof("Kafa broker %+v has valid configurations for the topic: %s", brokers, topic)
	return true
}
