// The common package contains the shared code between both producer and consumer

package common

import "os"

//StringInSlice is an O(n) function meant for use where performance is not critical (for unsorted slice)
func StringInSlice(s string, slce []string) bool {
	for _, a := range slce {
		if a == s {
			return true
		}
	}
	return false
}

//StringSliceEquals is an O(n) function meant for comparing two string slices
func StringSliceEquals(a, b []string) bool {
	// check length to make sure that elements are equal in O(1)
	if len(a) != len(b) {
		return false
	}
	// iterate and compare all elements in the slice
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

//Int32SliceEquals is an O(n) function meant for comparing two int32 slices
func Int32SliceEquals(a, b []int32) bool {
	// check length to make sure that elements are equal in O(1)
	if len(a) != len(b) {
		return false
	}
	// iterate and compare all elements in the slice
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

//Hostname returns the hostname or `unknown`
func Hostname() string {
	name, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return name
}
