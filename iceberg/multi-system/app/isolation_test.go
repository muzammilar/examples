package main

import "testing"

func TestLocalTargets(t *testing.T) {
	for _, settings := range []map[string]string{
		{},
		{
			"CATALOG_URL": "http://storage:8181", "S3_ENDPOINT": "storage:8333",
			"CLICKHOUSE_ADDR": "clickhouse:9000", "AWS_EC2_METADATA_DISABLED": "true",
		},
	} {
		if err := validateLocalTargets(func(key string) string { return settings[key] }); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRejectEndpointAndCredentialDiscoveryOverrides(t *testing.T) {
	for key, value := range map[string]string{
		"CATALOG_URL":               "https://not-an-owned-lab.invalid",
		"S3_ENDPOINT":               "not-an-owned-lab.invalid:443",
		"CLICKHOUSE_ADDR":           "not-an-owned-lab.invalid:9000",
		"AWS_EC2_METADATA_DISABLED": "false",
		"AWS_PROFILE":               "synthetic-profile", "AWS_DEFAULT_PROFILE": "synthetic-profile",
		"AWS_CONFIG_FILE": "/must-not-read", "AWS_SHARED_CREDENTIALS_FILE": "/must-not-read",
		"AWS_ACCESS_KEY_ID": "synthetic-key", "AWS_SECRET_ACCESS_KEY": "synthetic-secret",
		"AWS_SESSION_TOKEN": "synthetic-token", "AWS_WEB_IDENTITY_TOKEN_FILE": "/must-not-read",
		"AWS_ROLE_ARN": "synthetic-role", "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://not-an-owned-lab.invalid",
		"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/must-not-fetch",
		"AWS_ENDPOINT_URL":                       "https://not-an-owned-lab.invalid",
		"AWS_ENDPOINT_URL_S3":                    "https://not-an-owned-lab.invalid",
	} {
		t.Run(key, func(t *testing.T) {
			err := validateLocalTargets(func(candidate string) string {
				if candidate == key {
					return value
				}
				return ""
			})
			if err == nil {
				t.Fatal("accepted a non-local endpoint or credential-discovery setting")
			}
		})
	}
}
