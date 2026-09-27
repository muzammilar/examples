package main

import (
	"errors"
	"fmt"
	"io"
)

var ErrOutOfTea = errors.New("out of tea")
var ErrOutOfCoffee = errors.New("out of coffee")

func makeTea() error {
	// Simulate an error
	return fmt.Errorf("failed to boil water: %w and %w", ErrOutOfTea, ErrOutOfCoffee)
}

func main() {
	err := makeTea()

	if errors.Is(err, ErrOutOfTea) && errors.Is(err, ErrOutOfCoffee) {
		fmt.Println("We need to buy more tea and coffee!")
	} else if errors.Is(err, ErrOutOfCoffee) {
		fmt.Println("We need to buy more coffee!")
	} else if errors.Is(err, ErrOutOfTea) {
		fmt.Println("We need to buy more tea!")
	} else if errors.Is(err, io.EOF) {
		fmt.Println("End of file reached.")
	} else {
		fmt.Printf("An unexpected error occurred: %s\n", err)
	}
}
