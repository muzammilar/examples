package shreder

import (
	"bytes"
	"os"
	"testing"
)

const tmpFilePrefix = "tmpfile-"

/****************/
/*     Unit     */
/****************/

// (Unit Test) Test min function
func TestMin(t *testing.T) {
	testMin(t, 2, 3, 2)
	testMin(t, -1, -10, -10)
	testMin(t, 5, -1, -1)
	testMin(t, 0, 2, 0)
}

func testMin(t *testing.T, a, b, expected int64) {
	minVal := min(a, b)
	if minVal != expected {
		t.Fatalf("min(%d,%d), expected: %d, found: %d", a, b, expected, minVal)
	}
}

// (Unit Test) Test number of iterations = 0
func TestShredNZeroCount(t *testing.T) {

	if err := ShredN("fakepath", 0); err != nil {
		se, _ := err.(*ShredError) // ignore okay in test
		if se.Code != ErrInvalidIterationCount {
			t.Fatalf("ShredN with 0 iterations, expected: %s, found: %s", errInvalidItrationCount, se.Code.String())
		}
	}
}

/*****************/
/*    Helpers    */
/*****************/

func createTempFile(t *testing.T, data []byte) string {

	directory := t.TempDir()
	f, err := os.CreateTemp(directory, tmpFilePrefix)
	if err != nil {
		t.Fatal(err)
	}

	// write data to the temporary file
	if _, err := f.Write(data); err != nil {
		t.Fatal(err)
	}

	// cleanup function
	t.Cleanup(func() {
		// close and remove the temporary file at the end of the program
		f.Close()
		os.Remove(f.Name())
	})

	// get absolute file path of tmp file
	return f.Name()
}

// ensure that file size is same
func verifyFileOverwriteSize(t *testing.T, path string, expectedSize int64) {
	//verfy file size
	fileStat, err := os.Stat(path)
	if err != nil {
		t.Fatal(err.Error())
	}
	if expectedSize != fileStat.Size() {
		t.Fatalf("after overwrite for path: %s, expected file size: %d, actual file size: %d", path, expectedSize, fileStat.Size())
	}
}

// ensure that file contents are not same
func verifyFileOverwriteContentChange(t *testing.T, path string, data []byte) {
	filedata, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err.Error())
	}

	// compare file data
	if bytes.Compare(data, filedata) == 0 {
		t.Fatalf("data is identical after overwrite for path: %s", path)
	}
}

/*****************/
/*  Integration  */
/*****************/

// (Integration Test) Before all tests; create a directory and either create test files inside it
// or use tempfs to mount an already created image (with sample files)

// Test - Delete File - File is missing (before deletion)

// Test - Delete File - Permissions Issue (before deletion)

// Test - Delete File - Success - File must not path not exist after delete

// Test - Overwrite Once - The number of bytes written to the file are the same as length of the file (no more no less)
// for a file with no data blocks, a file with size smaller than the buffer (<1024 bytes), equal to the buffer, larger than the buffer, multiple of the buffer0

// Test - Overwrite Once - Write to a text file
func TestOverwriteOnceTextFile(t *testing.T) {
	data := []byte("abcdefghijklmonpqrstuvwxyz")
	expectedSize := len(data)
	path := createTempFile(t, data)
	// overwrite file
	if err := overwriteOnce(path, int64(expectedSize)); err != nil {
		t.Fatalf(err.Error())
	}
	// verify file size
	verifyFileOverwriteSize(t, path, int64(expectedSize))
	// read file to make sure contents are different (TODO: change this to hash computation)
	verifyFileOverwriteContentChange(t, path, data)
}

// Test - Overwrite Once - Write to a binary file (like image)

// Test - Overwrite Once - The file has some random binary data after being written (i.e. the file exists and doesn't have all 0s or 1s)

// Test - Overwrite Once - The hash of the file has changed (easy way to validate the file content changes)

// Test if path does not exist

// Test if path exists but has no data

// Test if the user does not have write permissions to the file

// Test a symlink

// Test a directory

// Test a pipe/special file/socket/device

// (Integration Test) After all tests; delete the test data directory (not needed)

/****************/
/*  Benchmarks  */
/****************/

// Benchmark the performance of a single iteration with a 1k, 10k, 100k, 1M, 10M, 100M, 1G files

// Benchmark O_SYNC performance

// Benchmark crypto/rand vs math/rand
