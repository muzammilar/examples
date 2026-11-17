package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"strconv"
)

type Product struct {
	ID         string `json:"id"`
	Tenant     int    `json:"tenant"`
	Stock      int    `json:"stock"`
	PriceCents int    `json:"price_cents"`
	Version    int    `json:"version"`
}

type Query struct {
	Text string `json:"text"`
}

type Fixture struct {
	Products []Product
	Queries  []Query
	Vectors  [][]float32
}

func readLines[T any](path string) ([]T, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	var values []T
	for scanner.Scan() {
		var value T
		if err := json.Unmarshal(scanner.Bytes(), &value); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		values = append(values, value)
	}
	return values, scanner.Err()
}

func loadFixture(root string) (*Fixture, error) {
	data, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return nil, err
	}
	var manifest struct {
		Parameters struct {
			Dimension int `json:"dimension"`
			Documents int `json:"documents"`
			Queries   int `json:"queries"`
		} `json:"parameters"`
		SHA256 map[string]string `json:"sha256"`
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return nil, err
	}
	if manifest.Parameters.Dimension != 384 {
		return nil, fmt.Errorf("expected 384-dimensional pinned corpus")
	}
	for _, name := range []string{"products.jsonl", "queries.jsonl", "vectors.f32", "query_vectors.f32", "exact.json"} {
		f, err := os.Open(filepath.Join(root, name))
		if err != nil {
			return nil, err
		}
		hash := sha256.New()
		_, copyErr := io.Copy(hash, f)
		closeErr := f.Close()
		if copyErr != nil {
			return nil, copyErr
		}
		if closeErr != nil {
			return nil, closeErr
		}
		if manifest.SHA256[name] != hex.EncodeToString(hash.Sum(nil)) {
			return nil, fmt.Errorf("corpus hash mismatch: %s", name)
		}
	}
	products, err := readLines[Product](filepath.Join(root, "products.jsonl"))
	if err != nil {
		return nil, err
	}
	queries, err := readLines[Query](filepath.Join(root, "queries.jsonl"))
	if err != nil {
		return nil, err
	}
	if len(products) != manifest.Parameters.Documents || len(queries) != manifest.Parameters.Queries || len(products) == 0 || len(queries) == 0 {
		return nil, fmt.Errorf("corpus counts differ from the pinned manifest")
	}
	for i, product := range products {
		if product.ID != strconv.Itoa(i) || product.Version != 0 || product.Tenant < 1 || product.Tenant > 100 {
			return nil, fmt.Errorf("invalid baseline product %d", i)
		}
	}
	raw, err := os.ReadFile(filepath.Join(root, "query_vectors.f32"))
	if err != nil {
		return nil, err
	}
	if len(raw) != len(queries)*384*4 {
		return nil, fmt.Errorf("invalid query-vector shape")
	}
	vectors := make([][]float32, len(queries))
	for i := range vectors {
		vectors[i] = make([]float32, 384)
		for j := range vectors[i] {
			offset := (i*384 + j) * 4
			v := math.Float32frombits(binary.LittleEndian.Uint32(raw[offset : offset+4]))
			if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
				return nil, fmt.Errorf("nonfinite query vector")
			}
			vectors[i][j] = v
		}
	}
	return &Fixture{Products: products, Queries: queries, Vectors: vectors}, nil
}

func (f *Fixture) product(id string, version int) (Product, error) {
	i, err := strconv.Atoi(id)
	if err != nil || i < 0 || i >= len(f.Products) || f.Products[i].ID != id || version < 0 {
		return Product{}, fmt.Errorf("invalid product identity/version %q/%d", id, version)
	}
	p := f.Products[i]
	if version > 0 {
		p.Version = version
		p.Stock = version % 100
		p.PriceCents += version
	}
	return p, nil
}
