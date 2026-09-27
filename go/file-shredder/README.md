# Shredder
This is an example to implement a `shred` function like the the [shred](https://manpages.ubuntu.com/manpages/jammy/man1/shred.1.html) command line utility.


### Run Tests
In order to run the test, run the following:
```sh
docker-compose up --build

```

### Known Limitations
* This program should never be run as superuser or `root` in order to avoid unintended consequences.
* Assumes that the file be overwritten "in-place" by the OS (i.e. the same data blocks are written to the same physical disk sectors). It ignores disk compaction and disk fragmentation or any other OS process that might move the data blocks from one disk sector to another (though my understanding of these concepts is fairly superficial).
* Assumes that files are not backed up externally (or in a different location) or in a snapshot from where we can recover the data (similar to `shred` utility).
* Assumes that the system is Disk I/O bound (mostly writes) bound. I am not sure if it would be possible to write to different parts of the file in parallel using seek (but I am not sure).
* Symlinks and Directories are not supported.
* Disk failures timeouts and deadlines are not handled.
* A better way to shred files are the standard utilities that have thoroughly tested, like `erase`, `wipe`, or `shred`.

#### Test Limitations
* Since a full test suite on this function has not been implemented, the current confidence of the capabilities of this package is _very_ low.
* Most  of the unimplemented tests are documented in the test file but haven't been implemented due to time limitations.
* The current test setup assumes that it has access to a filesystem and will therefore be able to run integration tests. It might be work separating the unit tests (and creating mocks where filesystem is not needed) and the integration tests (which depend on the filesystem).
* The current test setup reads the entire file as byte array (so it can't be scaled). A better way is to compute the hash (like SHA256) of the file before and after the write.
