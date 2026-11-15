package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"math"
	"math/rand"
)

type payload struct {
	Tenant int    `json:"tenant"`
	Kind   string `json:"kind"`
	Digest string `json:"digest"`
}
type point struct {
	ID       uint64    `json:"id"`
	Vector   []float32 `json:"vector"`
	Payload  payload   `json:"payload"`
	ShardKey string    `json:"shard_key,omitempty"`
}

func group(id uint64) string { return fmt.Sprintf("customers-%d", (id%100)%3) }

// A reproducible synthetic embedding corpus: 128 semantic-like clusters, with
// anisotropic coordinate scales and a shared bias. No semantic-quality claim.
func embedding(id uint64) []float32 {
	n := dimension()
	vector := make([]float32, n)
	centre := rand.New(rand.NewSource(int64(id%128) + 7000001))
	noise := rand.New(rand.NewSource(int64(id) ^ 42000017))
	norm := 0.0
	for d := range vector {
		scale := 1.0
		if d < 32 {
			scale = 3
		}
		value := scale*(centre.NormFloat64()+.3*noise.NormFloat64()) + .4
		vector[d] = float32(value)
		norm += value * value
	}
	norm = math.Sqrt(norm)
	for d := range vector {
		vector[d] = float32(float64(vector[d]) / norm)
	}
	return vector
}
func digest(id uint64, vector []float32) string {
	h := sha256.New()
	var word [8]byte
	binary.LittleEndian.PutUint64(word[:], id)
	_, _ = h.Write(word[:])
	for _, value := range vector {
		binary.LittleEndian.PutUint32(word[:4], math.Float32bits(value))
		_, _ = h.Write(word[:4])
	}
	return hex.EncodeToString(h.Sum(nil))
}
func makePoint(id uint64, kind string) point {
	vector := embedding(id)
	return point{ID: id, Vector: vector, Payload: payload{int(id % 100), kind, digest(id, vector)}}
}
func valid(p point, shard string) error {
	kind := "base"
	if p.ID >= uint64(count()) {
		kind = "recovery"
	}
	if group(p.ID) != shard || p.Payload.Tenant != int(p.ID%100) || p.Payload.Kind != kind || len(p.Vector) != dimension() {
		return fmt.Errorf("wrong shard, body or vector dimension for %d", p.ID)
	}
	var expected []float32
	var fingerprint string
	if p.ID < uint64(len(baseCache)) {
		expected = baseCache[p.ID]
		fingerprint = baseDigests[p.ID]
	} else {
		expected = embedding(p.ID)
		fingerprint = digest(p.ID, expected)
	}
	if p.Payload.Digest != fingerprint {
		return fmt.Errorf("changed payload checksum %d", p.ID)
	}
	for i, value := range p.Vector {
		if math.Float32bits(value) != math.Float32bits(expected[i]) {
			return fmt.Errorf("changed original float32 vector %d/%d", p.ID, i)
		}
	}
	return nil
}
