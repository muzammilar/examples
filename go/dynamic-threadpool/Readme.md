# Dynamic (Growing/Shrinking) Threadpool

A basic example of using Go Routines to create a dynamically growing and shrinking threadpool.

The example consists of two sets of goroutines;

* A single **controller** routine, that monitors the health of the threadpool and triggers a change in the size of the pool. The logic in this example is extremely primitive and uses a random number generator to increase/decrease the size.
  * Growing: When growing a threadpool, the controller creates new workers and add them to the `sync.WaitGroup` of the workers.
  * Shrinking: When shrinking a threadpool, the controller sends a signal over a channel to a worker with highest worker ID to cleanly shutdown of completing its work. This requires each worker to have a dedicated channel and allows for determinism in terms of removing a worker. The system also supports removing a random worker by using a shared channel (between all workers).
* A group of **workers** that perform user defined tasks and use a `select` statement to listen on channels and remove themselves from cleanly.

```sh
# build the containers
docker-compose up --build --detach

# delete the containers and their images
docker-compose down --rmi all --volumes
# Remove volumes
docker-compose rm --force --stop -v

```
