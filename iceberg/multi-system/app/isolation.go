package main

import "fmt"

// This lab has no cloud mode. Reject overrides before opening any engine or SDK.
func validateLocalTargets(getenv func(string) string) error {
	for _, target := range []struct{ key, address string }{
		{"CATALOG_URL", "http://storage:8181"},
		{"S3_ENDPOINT", "storage:8333"},
		{"CLICKHOUSE_ADDR", "clickhouse:9000"},
		{"AWS_EC2_METADATA_DISABLED", "true"},
	} {
		if value := getenv(target.key); value != "" && value != target.address {
			return fmt.Errorf("%s must use this example's fixed local setting", target.key)
		}
	}
	for _, key := range []string{
		"AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CONFIG_FILE",
		"AWS_SHARED_CREDENTIALS_FILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
		"AWS_SESSION_TOKEN", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
		"AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
		"AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3",
	} {
		if getenv(key) != "" {
			return fmt.Errorf("%s is forbidden: this runner uses only explicit lab credentials", key)
		}
	}
	return nil
}
