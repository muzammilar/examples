package main

import (
	"encoding/json"
	"fmt"
	"strconv"
)

type nativeRow map[string]json.RawMessage

func stringField(row nativeRow, key string) (string, error) {
	var value string
	err := json.Unmarshal(row[key], &value)
	if string(row[key]) == "null" || len(row[key]) == 0 {
		return "", fmt.Errorf("missing %s", key)
	}
	return value, err
}
func integerField(row nativeRow, key string) (int64, error) {
	return strconv.ParseInt(string(row[key]), 10, 64)
}
func wantString(row nativeRow, key, expected string) error {
	actual, err := stringField(row, key)
	if err != nil || actual != expected {
		return fmt.Errorf("%s differs: %s expected=%q", key, row[key], expected)
	}
	return nil
}
func wantInteger(row nativeRow, key string, expected int64) error {
	actual, err := integerField(row, key)
	if err != nil || actual != expected {
		return fmt.Errorf("%s differs: %s expected=%d", key, row[key], expected)
	}
	return nil
}
