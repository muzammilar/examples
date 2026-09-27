package shreder

import (
	"crypto/rand"
	"fmt"
	"os"
)

/***********/
/*  Error  */
/***********/

type ShredErrCode int

const bufferSize = 1024

const (
	ErrInvalidIterationCount ShredErrCode = iota
	ErrPathIsNotARegularFile
	ErrPathNotExists
	ErrShredderFileCorruption
)

const (
	errInvalidItrationCount    = "invalid iteration count"
	errPathIsNotAShredableFile = "not a shredable file"
	errPathNotExists           = "invalid path"
	errShredderFileCorruption  = "shredder corrupted the file"
)

func (f ShredErrCode) String() string {
	return [...]string{
		errInvalidItrationCount,
		errPathIsNotAShredableFile,
	}[f]
}

// ShredError implements externally visible error struct
type ShredError struct {
	Code ShredErrCode
}

func (s *ShredError) Error() string {
	return fmt.Sprintf("%s (code %d)", s.Code.String(), s.Code)
}

func newShredError(c ShredErrCode) *ShredError {
	return &ShredError{
		Code: c,
	}
}

/********************/
/*     External     */
/********************/

// Shred takes a file path, overwrites it thrice and then deletes the file
func Shred(path string) error {
	return ShredN(path, 3)
}

func ShredN(path string, iterations uint) error {

	// check iteration count
	if iterations <= 0 {
		return newShredError(ErrInvalidIterationCount)
	}

	// check if path exists
	fileStat, err := os.Stat(path)

	if err != nil {
		return err
	}
	// check if path is a directory or a special file
	// we don't shred them (even if everything is a file in linux)
	// Mask for the type bits. For regular files, none will be set. (https://pkg.go.dev/io/fs#FileInfo)
	// ModeType = ModeDir | ModeSymlink | ModeNamedPipe | ModeSocket | ModeDevice | ModeCharDevice | ModeIrregular
	// check if file is not a regular file
	if !fileStat.Mode().IsRegular() {
		return newShredError(ErrPathIsNotARegularFile)
	}

	fileSize := fileStat.Size()

	// iterate over the file and overwrite it
	var i uint
	for i = 0; i < iterations; i++ {
		// overwrite file
		if err := overwriteOnce(path, fileSize); err != nil {
			return err
		}
		//verfy file size
		fileStat, err := os.Stat(path)
		if err != nil {
			return err
		}
		if fileSize != fileStat.Size() {
			return newShredError(ErrShredderFileCorruption)
		}
	}

	// delete the file
	return deleteFile(path)
}

/**************/
/*  Internal  */
/**************/

// overwriteOnce writes n-bytes to a file
func overwriteOnce(path string, totalBytes int64) error {

	// check if file can be opened with read/write permissions
	// open file with sync IO so we can write the files in place (peformance not checked)
	fh, err := os.OpenFile(path, os.O_WRONLY|os.O_SYNC, 0)
	if err != nil {
		return err
	}
	defer fh.Close() // close file

	buf := make([]byte, bufferSize) // note: bytes.Buffer is fast when trying to reuse buffer
	var bytesWritten int64 = 0
	// overwrite file
	for bytesWritten < totalBytes {
		bytesToWrite := min(totalBytes-bytesWritten, bufferSize)
		bytesInBuffer, err := rand.Read(buf[:bytesToWrite]) // read buffer upto the max file size
		if err != nil {
			return err
		}
		// write buffer to the file
		//binary.Write(fh, binary.LittleEndian, buf[:bytesToWrite])
		if _, err := fh.Write(buf[:bytesToWrite]); err != nil {
			return err
		}

		// inrcement bytes written
		bytesWritten += int64(bytesInBuffer)
	}

	// write flush on the file so that all the contents are written to the file though it shouldn't be needed due to O_SYNC
	return fh.Sync()
}

// deletes a file if it exists or returns an error. No guard rails are present so it must not be run as root.
func deleteFile(path string) error {

	return os.Remove(path)
}

func min(a, b int64) int64 {
	if a > b {
		return b
	}
	return a
}
