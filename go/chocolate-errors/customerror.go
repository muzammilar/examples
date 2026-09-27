package main

import (
	"fmt"
	"math/rand"
	"time"
)

// Anything that implements the error interface can be an error!

// Probably not best practice to use capitalized letters and symbols in error strings

func init() {
	rand.Seed(time.Now().Unix())
}

//EmptyError struct
type EmptyError struct{}

//
func (e *EmptyError) Error() string {
	return fmt.Sprintf("no M&Ms found")
}

//JustificationError struct
type JustificationError struct {
	question string
	proof    string
}

func NewJustificationError(question, proof string) *JustificationError {
	return &JustificationError{
		question: question,
		proof:    proof,
	}
}

func (j *JustificationError) Error() string {
	return fmt.Sprintf("%T: %s - %s", j, j.question, j.proof)
}

// HealthySnacks struct
type HealthySnacks struct {
	snacks string
}

func NewHealthySnacks() *HealthySnacks {
	return new(HealthySnacks)
}

func (h *HealthySnacks) Error() string {
	return fmt.Sprintf("%T: Have you tried to eat something healthy instead? Celery sticks are usually good.", h)
}

/////////////////////////////////////////////////////////////////////

func GetRandomError() error {

	r := rand.Int() % 13
	if r <= 3 {
		return NewHealthySnacks()
	}
	if r <= 6 {
		return NewJustificationError("Stop Snickering Dr.Mars, there's Bounty on your head!", "It's Rocher - Ferrero Rocher, actually, and start Lindt-ing your code!")
	}
	if r <= 9 {
		return &EmptyError{}
	}
	return fmt.Errorf("Nothing fun here. Just a generic error. Ooh! A Toblerone!")
}

/////////////////////////////////////////////////////////////////////

func main() {

	for {
		err := GetRandomError()
		switch errV := err.(type) {
		case *HealthySnacks: // dove twist reese's arm, maybe?
			fmt.Println("Forget Vim vs Emacs, I think I just a saw Dove Twix Reese's arm!")
		case *EmptyError:
			fmt.Printf("If you're looking for candy, %s i.e. %#v \n", errV, errV)
		case *JustificationError:
			fmt.Println(errV)
		default:
			fmt.Println(errV.Error())
		}

		if healthy, ok := err.(*HealthySnacks); ok {
			fmt.Printf("Godiva or Ghirardelli? %s\n", healthy)
		}

		time.Sleep(200 * time.Millisecond)
	}

}
