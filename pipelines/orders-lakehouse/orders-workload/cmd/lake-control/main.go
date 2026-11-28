// Native Kafka/S3/catalog administration for this isolated example.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"examples/orders-lakehouse/internal/observe"
	"fmt"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"
	"github.com/jackc/pgx/v5"
	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kerr"
	"github.com/twmb/franz-go/pkg/kgo"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"syscall"
	"time"
)

func emit(value any) {
	if e := json.NewEncoder(os.Stdout).Encode(value); e != nil {
		panic(e)
	}
}
func properties() (map[string]string, error) {
	body, e := os.ReadFile("/app/catalog.json")
	if e != nil {
		return nil, e
	}
	var p map[string]string
	if e = json.Unmarshal(body, &p); e != nil {
		return nil, e
	}
	if p["uri"] != "jdbc:postgresql://catalog-db:5432/lake?sslmode=disable" || p["s3.endpoint"] != "http://s3:8333" || p["warehouse"] != "s3://warehouse/" || p["s3.access-key-id"] != "lakehouse-lab" || p["s3.secret-access-key"] != "lakehouse-local-only-secret" {
		return nil, fmt.Errorf("unexpected endpoint/credentials; public-cloud fallback forbidden")
	}
	return p, nil
}
func postgres(ctx context.Context, p map[string]string) (*pgx.Conn, error) {
	u, e := url.Parse("postgresql://catalog-db:5432/lake?sslmode=disable&connect_timeout=5&application_name=lake-control")
	if e != nil {
		return nil, e
	}
	u.User = url.UserPassword(p["jdbc.user"], p["jdbc.password"])
	return pgx.Connect(ctx, u.String())
}
func s3client(p map[string]string) *s3.Client {
	return s3.New(s3.Options{Region: p["client.region"], BaseEndpoint: aws.String(p["s3.endpoint"]), UsePathStyle: true, Credentials: credentials.NewStaticCredentialsProvider(p["s3.access-key-id"], p["s3.secret-access-key"], ""), HTTPClient: &http.Client{Timeout: 20 * time.Second}})
}
func kafka(ctx context.Context, create bool) error {
	client, e := kgo.NewClient(kgo.SeedBrokers("broker1:9092", "broker2:9092", "broker3:9092"), kgo.ClientID("lake-native-audit"))
	if e != nil {
		return e
	}
	defer client.Close()
	admin := kadm.NewClient(client)
	if create {
		value := "2"
		result, e := admin.CreateTopics(ctx, 3, 3, map[string]*string{"min.insync.replicas": &value}, "lake-orders")
		if e != nil {
			return e
		}
		for _, r := range result {
			if r.Err != nil && !errors.Is(r.Err, kerr.TopicAlreadyExists) {
				return r.Err
			}
		}
	}
	topics, e := admin.ListTopics(ctx, "lake-orders")
	if e != nil {
		return e
	}
	topic, ok := topics["lake-orders"]
	if !ok || topic.Err != nil || len(topic.Partitions) != 3 {
		return fmt.Errorf("three topic partitions required: %v", topics)
	}
	for _, part := range topic.Partitions {
		if part.Err != nil || len(part.Replicas) != 3 || len(part.ISR) != 3 || part.Leader < 0 {
			return fmt.Errorf("partition not current RF3: %v", part)
		}
	}
	configs, e := admin.DescribeTopicConfigs(ctx, "lake-orders")
	if e != nil {
		return e
	}
	effective, err := configs.On("lake-orders", nil)
	if err != nil || effective.Err != nil {
		return fmt.Errorf("missing topic config: %v / %v", err, effective.Err)
	}
	values := map[string]string{}
	for _, entry := range effective.Configs {
		if entry.Value != nil {
			values[entry.Key] = *entry.Value
		}
	}
	if values["min.insync.replicas"] != "2" || values["unclean.leader.election.enable"] != "false" {
		return fmt.Errorf("unexpected Kafka durability: %v", values)
	}
	emit(map[string]any{"topic": topics, "native_configs": configs})
	return nil
}
func run() error {
	if len(os.Args) == 2 && os.Args[1] == "cgroup" {
		observe.Raw()
		return nil
	}
	defer observe.Footer()
	if len(os.Args) < 2 {
		return fmt.Errorf("expected mode")
	}
	p, e := properties()
	if e != nil {
		return e
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	switch mode := os.Args[1]; mode {
	case "inventory":
		client := s3client(p)
		pages := s3.NewListObjectsV2Paginator(client, &s3.ListObjectsV2Input{Bucket: aws.String("warehouse")})
		objects := []map[string]any{}
		var bytes int64
		for pages.HasMorePages() {
			page, err := pages.NextPage(ctx)
			if err != nil {
				return err
			}
			for _, item := range page.Contents {
				objects = append(objects, map[string]any{"key": aws.ToString(item.Key), "bytes": aws.ToInt64(item.Size)})
				bytes += aws.ToInt64(item.Size)
			}
		}
		emit(map[string]any{"objects": objects, "logical_object_bytes": bytes})
		return nil
	case "setup-kafka":
		return kafka(ctx, true)
	case "audit-kafka":
		return kafka(ctx, false)
	case "setup-bucket":
		client := s3client(p)
		_, e := client.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: aws.String("warehouse")})
		if e != nil {
			var api smithy.APIError
			if !errors.As(e, &api) || (api.ErrorCode() != "NotFound" && api.ErrorCode() != "NoSuchBucket") {
				return e
			}
			if _, e = client.CreateBucket(ctx, &s3.CreateBucketInput{Bucket: aws.String("warehouse")}); e != nil {
				return e
			}
		}
		_, e = client.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: aws.String("warehouse")})
		if e != nil {
			return e
		}
		emit(map[string]any{"passed": true, "bucket": "warehouse", "endpoint": p["s3.endpoint"]})
		return nil
	case "metadata", "blocked", "settings", "lock":
		db, e := postgres(ctx, p)
		if e != nil {
			return e
		}
		defer db.Close(context.Background())
		if mode == "settings" {
			var version, fsync, commit, full string
			if e = db.QueryRow(ctx, "SELECT version(),current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes')").Scan(&version, &fsync, &commit, &full); e != nil {
				return e
			}
			if fsync != "on" || commit != "on" || full != "on" {
				return fmt.Errorf("weakened catalog durability")
			}
			emit(map[string]any{"version": version, "fsync": fsync, "synchronous_commit": commit, "full_page_writes": full})
			return nil
		}
		if mode == "metadata" {
			var location string
			if e = db.QueryRow(ctx, "SELECT metadata_location FROM iceberg_tables WHERE catalog_name='lake' AND table_namespace='lab' AND table_name='orders'").Scan(&location); e != nil {
				return e
			}
			if len(location) < len("s3://warehouse/") || location[:len("s3://warehouse/")] != "s3://warehouse/" {
				return fmt.Errorf("unexpected metadata endpoint")
			}
			emit(map[string]any{"metadata_location": location})
			return nil
		}
		if mode == "blocked" {
			rows, e := db.Query(ctx, "SELECT pid,client_addr::text,wait_event_type,query,pg_blocking_pids(pid) FROM pg_stat_activity WHERE application_name='lake-flink' AND wait_event_type='Lock'")
			if e != nil {
				return e
			}
			defer rows.Close()
			out := []map[string]any{}
			for rows.Next() {
				var pid int32
				var host, wait, query string
				var blockers []int32
				if e = rows.Scan(&pid, &host, &wait, &query, &blockers); e != nil {
					return e
				}
				out = append(out, map[string]any{"pid": pid, "host": host, "wait": wait, "query": query, "blockers": blockers})
			}
			if e = rows.Err(); e != nil {
				return e
			}
			emit(map[string]any{"blocked": out})
			return nil
		}
		if len(os.Args) != 3 || !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(os.Args[2]) {
			return fmt.Errorf("lock requires owned case ID")
		}
		tx, e := db.Begin(ctx)
		if e != nil {
			return e
		}
		defer tx.Rollback(context.Background())
		if _, e = tx.Exec(ctx, "SET LOCAL idle_in_transaction_session_timeout='90s'"); e != nil {
			return e
		}
		var location string
		if e = tx.QueryRow(ctx, "SELECT metadata_location FROM iceberg_tables WHERE catalog_name='lake' AND table_namespace='lab' AND table_name='orders' FOR UPDATE").Scan(&location); e != nil {
			return e
		}
		value := map[string]any{"case": os.Args[2], "pid": db.PgConn().PID(), "metadata_location": location, "held_at": time.Now().UTC().Format(time.RFC3339Nano)}
		file, e := os.OpenFile(filepath.Join("/witness", "lock-"+os.Args[2]+".json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			return e
		}
		if e = json.NewEncoder(file).Encode(value); e != nil {
			file.Close()
			return e
		}
		if e = file.Sync(); e != nil {
			file.Close()
			return e
		}
		file.Close()
		emit(value)
		signals, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
		defer stop()
		select {
		case <-signals.Done():
		case <-time.After(80 * time.Second):
			return fmt.Errorf("controlled row-lock observation expired")
		}
		return tx.Rollback(context.Background())
	default:
		return fmt.Errorf("unknown mode: %s", mode)
	}
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
