package main

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"net"
	"sync"
	"time"

	clickhouse "github.com/ClickHouse/clickhouse-go"
)

func main() {
	// add a wait group for all the routines
	var wg sync.WaitGroup
	wg.Add(2)

	// run the queries
	go exampleIPv4Table(&wg)
	go exampleIPv6Table(&wg)

	// done
	wg.Wait()

}

func exampleIPv4Table(wg *sync.WaitGroup) {

	// Call done on the wait group
	defer wg.Done()

	var connect *sql.DB = createConnection()
	defer connect.Close()

	var createQuery string = `
		CREATE TABLE IF NOT EXISTS example_ipv4 (
			country_code FixedString(2),
			os_id        UInt8,
			browser_id   UInt8,
			browser_ip   IPv4,
			categories   Array(Int16),
			action_day   Date,
			action_time  DateTime
		) engine=Memory
	`

	createTable(connect, createQuery)

	var (
		tx, _   = connect.Begin()
		stmt, _ = tx.Prepare("INSERT INTO example_ipv4 (country_code, os_id, browser_id, browser_ip, categories, action_day, action_time) VALUES (?, ?, ?, ?, ?, ?, ?)")
	)
	defer stmt.Close()

	// create a bulk ingest data
	for i := 0; i < 100; i++ {
		if _, err := stmt.Exec(
			"US",
			10+i,
			100+i,
			"127.0.0.1",
			clickhouse.Array([]int16{1, 2, 3}),
			time.Now(),
			time.Now(),
		); err != nil {
			log.Fatal(err)
		}

	}

	// send bulk data to clickhouse
	if err := tx.Commit(); err != nil {
		log.Fatal(err)
	}

	queryTable(connect, "example_ipv4")
	dropTable(connect, "example_ipv4")
}

func exampleIPv6Table(wg *sync.WaitGroup) {

	// Call done on the wait group
	defer wg.Done()

	connect := createConnection()
	defer connect.Close()

	var createQuery string = `
		CREATE TABLE IF NOT EXISTS example_ipv6 (
			country_code LowCardinality(String),
			os_id        UInt8,
			browser_id   UInt8,
			browser_ip   IPv6,
			categories   Array(Int16),
			action_day   Date,
			action_time  DateTime
		) engine=Memory
	`

	createTable(connect, createQuery)

	var (
		tx, _   = connect.Begin()
		stmt, _ = tx.Prepare("INSERT INTO example_ipv6 (country_code, os_id, browser_id, browser_ip, categories, action_day, action_time) VALUES (?, ?, ?, ?, ?, ?, ?)")
	)
	defer stmt.Close()

	// create a bulk ingest data
	for i := 0; i < 200; i++ {
		if _, err := stmt.Exec(
			"USA",
			20+i,
			200+i,
			net.ParseIP("8726:1153:fe8d::154b"),
			clickhouse.Array([]int16{4, 5, 6}),
			time.Now(),
			time.Now(),
		); err != nil {
			log.Fatal(err)
		}

	}

	// send bulk data to clickhouse
	if err := tx.Commit(); err != nil {
		log.Fatal(err)
	}

	queryTable(connect, "example_ipv6")
	dropTable(connect, "example_ipv6")

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

	// return nothing
	return nil
}

func createTable(connect *sql.DB, createQ string) {

	_, err := connect.Exec(createQ)

	if err != nil {
		log.Fatal(err)
	}

}

func queryTable(connect *sql.DB, tableName string) {

	query := fmt.Sprintf("SELECT country_code, os_id, browser_id, browser_ip, categories, action_day, action_time FROM %s", tableName)
	rows, err := connect.Query(query)
	if err != nil {
		log.Fatal(err)
	}
	defer rows.Close()

	for rows.Next() {
		var (
			ip                    net.IP
			country               string
			os, browser           uint8
			categories            []int16
			actionDay, actionTime time.Time
		)
		if err := rows.Scan(&country, &os, &browser, &ip, &categories, &actionDay, &actionTime); err != nil {
			log.Fatal(err)
		}
		log.Printf("country: %s, os: %d, browser: %d, categories: %v, action_day: %s, action_time: %s", country, os, browser, categories, actionDay, actionTime)
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
