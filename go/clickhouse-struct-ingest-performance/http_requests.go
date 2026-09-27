package main

import (
	"fmt"
	"math/rand"
	"net"
	"reflect"
	"strings"
	"time"

	clickhouse "github.com/ClickHouse/clickhouse-go"
)

// Structs
type HTTPRequest struct {
	CountryCode string      `db:"country_code"`
	OsID        uint8       `db:"os_id"`
	BrowserID   *uint8      `db:"browser_id"`
	Categories  interface{} `db:"categories"` // clickhouse array
	ActionTime  time.Time   `db:"action_time"`
	ActionDay   time.Time   `db:"action_day"`
	BrowserIP   net.IP      `db:"browser_ip"`
	ReturnCode  string      `db:"return_code"`
}

// NewHTTPRequest
func NewHTTPRequest() *HTTPRequest {
	// return codes
	returnCodes := [...]string{"200", "300", "400", "500"}
	returnCodesLen := len(returnCodes)

	httpRequest := new(HTTPRequest)
	httpRequest.CountryCode = "USA"
	httpRequest.OsID = uint8(rand.Intn(100))
	httpRequest.BrowserIP = net.ParseIP("8726:1153:fe8d::154b")
	httpRequest.ActionDay = time.Now()
	httpRequest.ActionTime = time.Now()
	httpRequest.ReturnCode = returnCodes[rand.Int()%returnCodesLen]
	httpRequest.Categories = clickhouse.Array([]int16{4, 5, 6})

	// mix nil and rand integers
	if browserId := rand.Intn(10); browserId < 5 {
		httpRequest.BrowserID = new(uint8)
		*httpRequest.BrowserID = uint8(browserId)
	}

	return httpRequest
}

// A relatively expensive function to print all the fields (when done on large dataset).
// It should be only used for creating the data for `ConvertToSlice`
// This will need a special `main` function with the following
// var h HTTPRequest
// h.printFields()
func (h HTTPRequest) printFields() {

	t := reflect.TypeOf(h)
	n := t.NumField()
	for i := 0; i < n; i++ {
		fmt.Printf("h.%s,\n", t.Field(i).Name)
	}
}

// Create a function that converts a struct to slice
func (h *HTTPRequest) ConvertToSlice() []interface{} {
	// Alternative is to use `reflect` package, but reflect might be a bit slow
	// make sure the order is the same as the order in the struct
	// you can probably use the reflect package to generate print the structs one time though

	return []interface{}{
		h.CountryCode,
		h.OsID,
		h.BrowserID,
		h.Categories,
		h.ActionTime,
		h.ActionDay,
		h.BrowserIP,
		h.ReturnCode,
	}
}

// Create an insert statement from a struct (using reflect is okay since it's not a performance critical path)
func GetInsertStatement(tableName string, arg interface{}) (string, error) {

	fields := make([]string, 0, 5)
	values := make([]string, 0, 5)

	t := reflect.TypeOf(arg)
	n := t.NumField()
	for i := 0; i < n; i++ {
		f := t.Field(i)
		columnName := f.Tag.Get("db")
		// no tag
		if len(columnName) == 0 {
			return "", fmt.Errorf("No `db` tag found for field `%s` of struct `%T`", f.Name, arg)
		}
		fields = append(fields, columnName)
		values = append(values, "?")
	}

	return fmt.Sprintf("INSERT INTO %s (%s) VALUES (%s)",
		tableName,
		strings.Join(fields, ", "),
		strings.Join(values, ", ")), nil

}
