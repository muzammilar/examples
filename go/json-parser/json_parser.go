package main

import (
	"encoding/json"
	"fmt"
)

//MessageA struct
type MessageA struct {
	FieldX       int    `json:"field_x"`
	FieldY       int    `json:"field_y"`
	SimilarField string `json:"similiarly_named_field"`
}

//MessageB struct
type MessageB struct {
	FieldZ       int `json:"field_z"`
	SimilarField int `json:"similiarly_named_field"`
}

//MessageC struct
type MessageC struct {
	FieldZ       int    `json:"field_z"`
	SimilarField string `json:"similiarly_named_field"`
}

//MessageI interface
type MessageI interface{}

//Message struct to contain generic messages
type Message struct {
	MessageI
}

// UnmarshalJSON function to convert JSON to a struct
func (msg *Message) UnmarshalJSON(data []byte) error {
	var m map[string]interface{}
	if err := json.Unmarshal(data, &m); err != nil {
		return err
	}
	t := m["type"].(string)
	switch t {
	case "A":
		msg.MessageI = &MessageA{}
	case "B":
		msg.MessageI = &MessageB{}
	default:
		msg.MessageI = &MessageC{}
	}

	// convert a map to struct
	return json.Unmarshal(data, msg.MessageI)
}

func main() {
	var src = `[{"type": "UNKNWONW", "field_x": 134, "field_z": 456, "similiarly_named_field": "something here"}, {"type": "A", "field_x": 134, "field_y": 456, "similiarly_named_field": "something here"}, {"type": "B", "field_z": 888, "similiarly_named_field": 123}]`

	var msgs []Message
	err := json.Unmarshal([]byte(src), &msgs)
	if err != nil {
		fmt.Println(err)
		return
	}

	// print messages and the original
	fmt.Printf("Original: %s\n", src)

	msgsJSON, err := json.Marshal(msgs)
	if err != nil {
		fmt.Println(err)
		return
	}

	fmt.Printf("Parsed: %s\n", string(msgsJSON))

	// print messages
	for i, msg := range msgs {
		fmt.Printf("Message: %d \tType: %T \t Value: %v\n", i, msg.MessageI, msg.MessageI)
	}
}
