# Benchmakr IP Firewall Update Performance
This example also provides the performance benchmarks for different mechanisms to notify the readers about update of a variables storing some data (atomic pointers for the variables, atomic pointers for the version numbers, contexts with cancelations, mutexes).

The *primary* reason for this example is to benchmark the performance difference of the approaches considered from the reader's perspective (and not the writer/reloader/updater perspective).

### Run Tests
In order to run the test, run the following:
```sh
docker-compose up --build

```

## Approaches considered

For our benchmarks we consider multiple approaches as follows:

**Atomic Updates of IP List Variable:** Using an pointer to the IP Addresses/IP Networks/IP Tree variable and atomically updating it. Reads are also atomic.

**Logical Clock/Atomic Updates of Version Numbers:** Using a logical clock to track the version number of the IP Addresses/IP Networks/IP Tree and atomically updating it from the writer. In this approach, once the version number changes/increases, the IP Addresses/IP Networks/IP Tree variable is updated (using atomic instructions). The advantage of this approach is that it allows the readers to keep track of their version number. In this approach, the version number is incremented *after* updating the variable containing the IP Addresses/IP Networks/IP Tree in order to prevent any race conditions where readers might read an older version of the IP Addresses/IP Networks/IP Tree variable.

**Multi-Reader Single-Writer Mutexes:** Using `sync.RWMutex` in golang.

**Mutexes:** Using `sync.Mutex` in golang.

**Channels:** Using a channel of the pointer. This approach requires the write to know exactly the number of readers and is prone to race conditions with multiple readers (so it's only feasible for a single reader).

**Contexts:** Using a context to update the readers about the `IPFirewall` variable being changed. Contexts and channels are somewhat similar since contexts generally use a channel (as of Go 1.18) for their their `Done` function.

### Results:
This [link](https://stackoverflow.com/questions/57562606/why-does-sync-mutex-largely-drop-performance-when-goroutine-contention-is-more-t) has some good graphs about performances.
