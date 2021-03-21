package main

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"math/rand"
	"net"
	"sync"
	"time"

	clickhouse "github.com/ClickHouse/clickhouse-go"
)

func main() {
	// initialize global pseudo random generator
	rand.Seed(time.Now().Unix())

	// add a wait group for all the routines (useful for testing thread interleaving)
	var wg sync.WaitGroup
	wg.Add(1)

	// ingest 100k rows (can be modified to performance testing)
	go exampleIPv6Table(&wg, 100000)

	// done
	wg.Wait()

}

func exampleIPv6Table(wg *sync.WaitGroup, dataSize uint32) {

	// Call done on the wait group
	defer wg.Done()

	// strut to store time durations (and print execution times)
	var (
		executionTimes        = make(map[string]time.Duration)
		tableName      string = fmt.Sprintf("example_ipv6_%d", dataSize)
	)
	defer printExecutionTimes(executionTimes, dataSize)

	// setup a connection to the database
	connect := createConnection()
	defer connect.Close()

	var createQueryTempalate string = `
		CREATE TABLE IF NOT EXISTS %s (
			country_code LowCardinality(String),
			os_id        UInt8,
			browser_id   Nullable(UInt8),
			browser_ip   IPv6,
			return_code  Enum8('200'=1, '300'=2, '400'=3, '500'=4),
			categories   Array(Int16),
			action_day   Date,
			action_time  DateTime
		) engine=Memory
	`
	var createQuery string = fmt.Sprintf(createQueryTempalate, tableName)

	// create table (and time it)
	start := time.Now()
	createTable(connect, createQuery)
	executionTimes["Table Creation Time"] = time.Since(start)

	// clickhouse-go ignores the fields after the `VALUES` statement, so it doesn't matter if you use bind variables (:field_name) or a `?`
	// see the code: https://github.com/ClickHouse/clickhouse-go/blob/adf448e268b7f8b32880128923ce689d05e9b2e5/clickhouse.go#L91
	// This behaviour may change in the future though
	var (
		httpRequest HTTPRequest
		tx, _       = connect.Begin()
		insertQ, _  = GetInsertStatement(tableName, httpRequest)
		stmt, _     = tx.Prepare(insertQ)
	)
	defer stmt.Close()

	// create a bulk ingest data (and time it)
	start = time.Now()
	var idx uint32
	for idx = 0; idx < dataSize; idx++ {
		// stmt.Exec using variadic args so we create a slice then make it variadic (see the ... at the end)
		// see: https://gobyexample.com/variadic-functions
		inputHTTPRequest := NewHTTPRequest()
		if _, err := stmt.Exec(inputHTTPRequest.ConvertToSlice()...); err != nil {
			log.Fatal(err)
		}

	}
	executionTimes["Batch Prepration Time (includes struct creation time)"] = time.Since(start)

	// send bulk data to clickhouse (and time it)
	start = time.Now()
	if err := tx.Commit(); err != nil {
		log.Fatal(err)
	}
	executionTimes["Batch Insert Time"] = time.Since(start)

	queryTable(connect, tableName)
	dropTable(connect, tableName)

}

func createConnection() *sql.DB {

	// create a sql.DB handle
	connect, err := sql.Open("clickhouse", "tcp://clickhouse-server-1:9000?debug=true")
	if err != nil {
		log.Fatal(err)
	}

	// connect to the clickhosue server	and keep trying until you succeed
	var (
		backOffTimeMs    uint32 = 50
		maxBackOffTimeMs uint32 = 5000
	)
	for {
		if err := connect.Ping(); err != nil {
			if exception, ok := err.(*clickhouse.Exception); ok {
				fmt.Printf("[%d] %s \n%s\n", exception.Code, exception.Message, exception.StackTrace)
			} else {
				fmt.Println(err)
			}
			time.Sleep(time.Duration(backOffTimeMs) * time.Millisecond)
			backOffTimeMs = uint32(math.Min(float64(maxBackOffTimeMs), float64(2*backOffTimeMs)))
			continue
		}
		return connect
	}

}

func createTable(connect *sql.DB, createQ string) {

	_, err := connect.Exec(createQ)

	if err != nil {
		log.Fatal(err)
	}

}

func queryTable(connect *sql.DB, tableName string) {

	// For querying and parsing data to structs, check out `sqlx` package in golang.
	// `Select *` is generally not best practice, especially for clickhouse (MergeTrees), but we'll ignore it for now
	query := fmt.Sprintf("SELECT country_code, os_id, browser_id, browser_ip, categories, action_day, action_time FROM %s", tableName)
	rows, err := connect.Query(query)
	if err != nil {
		log.Fatal(err)
	}
	defer rows.Close()

	for rows.Next() {
		var (
			ip                    net.IP
			country, browser_id   string
			os                    uint8
			browser               *uint8
			categories            []int16
			actionDay, actionTime time.Time
		)
		if err := rows.Scan(&country, &os, &browser, &ip, &categories, &actionDay, &actionTime); err != nil {
			log.Fatal(err)
		}

		// check for nil (only for printing)
		if browser != nil {
			browser_id = fmt.Sprintf("%d", *browser)
		} else {
			browser_id = fmt.Sprintf("%#v", browser)
		}

		log.Printf("country: %s, os: %d, browser: %s, categories: %v, action_day: %s, action_time: %s, browser_ip: %s", country, os, browser_id, categories, actionDay, actionTime, ip)
	}

	if err := rows.Err(); err != nil {
		log.Fatal(err)
	}
}

func dropTable(connect *sql.DB, tableName string) {

	var query string = fmt.Sprintf("DROP TABLE %s", tableName)
	if _, err := connect.Exec(query); err != nil {
		log.Fatal(err)
	}

}

func printExecutionTimes(et map[string]time.Duration, s uint32) {
	for event, duration := range et {
		log.Printf("[Data Size:%d] %v: %v\n", s, event, duration)

	}
}
