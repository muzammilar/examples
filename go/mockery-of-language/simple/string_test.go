package simple

import (
	"testing"

	"github.com/muzammilar/mockeryoflang/simple/mocksimple"
	"github.com/stretchr/testify/assert"
)

func Foo(s Stringer) string {
	return s.String()
}

func TestString(t *testing.T) {
	mockStringer := mocksimple.NewMockStringer(t)
	mockStringer.EXPECT().String().Return("mockery")
	assert.Equal(t, "mockery", Foo(mockStringer))
}
