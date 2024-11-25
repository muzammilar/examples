package main

import (
	"fmt"
	"net/http"
)

// handler function that returns a "Hello, World!" message
func helloHandler(w http.ResponseWriter, r *http.Request) {
	fmt.Fprintln(w, "Hello, World!")
}

func main() {
	// Register the handler function with the "/hello" path
	http.HandleFunc("/hello", helloHandler)

	// Start the web server on port 8080
	fmt.Println("Starting server on :8080...")
	if err := http.ListenAndServe(":8080", nil); err != nil {
		fmt.Println("Error starting server:", err)
	}
}
