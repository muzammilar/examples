// The periadic printer (or pprinter) is a basic foobar application
package main

import (
	"fmt"
	"time"
)

var date string

func main() {
	for {
		fmt.Printf("Hello! This program was compiled on `%s`.\n", date)
		time.Sleep(30 * time.Second)
	}

}
