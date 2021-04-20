package main

import (
	"fmt"
	"log"
	"reflect"
	"strings"
)

//MessageBase struct
type MessageBaseA struct {
	FieldA int    `msgtag:"field_a"`
	FieldB uint32 `msgtag:"field_b"`
}

//MessageBaseB struct
type MessageBaseB struct {
	MessageBaseA
	FieldK string `msgtag:"field_k"`
	FieldL uint32 `msgtag:"field_l"`
	FieldM uint   `msgtag:"field_m"`
}

//MessageB struct
type Message struct {
	MessageBaseA
	MessageBaseB
	FieldX int    `msgtag:"field_x"`
	FieldY uint   `msgtag:"field_y"`
	FieldZ string `msgtag:"field_z"`
}

//NewMessageBaseA
func NewMessageBaseA() MessageBaseA {
	return MessageBaseA{
		FieldA: 1,
		FieldB: 2,
	}
}

func NewMessageBaseB() MessageBaseB {
	return MessageBaseB{
		MessageBaseA: MessageBaseA{
			FieldA: 5,
			FieldB: 6,
		},
		FieldK: "8",
		FieldL: 9,
		FieldM: 10,
	}
}

func NewMessage() Message {
	return Message{
		MessageBaseA: NewMessageBaseA(),
		MessageBaseB: NewMessageBaseB(),
		FieldX:       101,
		FieldY:       102,
		FieldZ:       "103",
	}
}

//GetFlattenedTags gets a list of tags including from embedded structs
// Note use of reflect on large datasets is expensive
func GetFlattenedTags(tagName string, parentField string, iface interface{}) []string {
	fields := make([]string, 0)
	ifv := reflect.ValueOf(iface)
	ift := reflect.TypeOf(iface)

	for i := 0; i < ift.NumField(); i++ {
		v := ifv.Field(i)
		t := ift.Field(i)

		switch v.Kind() { // switch on the kind of the value (structs need recursion)
		case reflect.Struct:
			switch t.Anonymous { // check for embedded structs only
			case true:
				structField := fmt.Sprintf("%s.%s", parentField, t.Name)
				fields = append(fields, GetFlattenedTags(tagName, structField, v.Interface())...)
			default: // this example only allows embedded structs
				log.Fatalf("Only Embedded Structs supported. Found non-embedded struct: %v", v)
			}
		default: // print the information including the parentField name
			field := fmt.Sprintf("%s.%s\t\t\t %s\t %s", parentField, t.Name, t.Type.Name(), t.Tag.Get(tagName))
			fields = append(fields, field)
		}
	}

	return fields
}

func main() {
	var sb strings.Builder

	msg := NewMessage()

	// get all the tags and all the variables
	tagsSlice := GetFlattenedTags("msgtag", "msg", msg)

	// print the information about the embedded struct
	sb.WriteString(fmt.Sprintf("Details of the `Message` Struct:\n"))
	sb.WriteString(fmt.Sprintf("Variable Name\t\t\t Type\t Tag\n"))
	for _, i := range tagsSlice {
		sb.WriteString(fmt.Sprintf("%s\n", i))
	}
	fmt.Println(sb.String())

}
