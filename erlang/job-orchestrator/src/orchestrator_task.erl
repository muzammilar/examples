-module(orchestrator_task).
-moduledoc "The work a job can do.".

-export([run/1]).
-export_type([task/0, result/0]).

-type task() :: {fib, non_neg_integer()} | {sleep, non_neg_integer()} | crash.
-type result() :: term().

-doc "Runs `Task` in the calling process. `crash` raises an error.".
-spec run(task()) -> result().
run({fib, N}) ->
    fib(N, 0, 1);
run({sleep, Ms}) ->
    timer:sleep(Ms),
    ok;
run(crash) ->
    error(crash_requested).

fib(0, A, _) -> A;
fib(N, A, B) -> fib(N - 1, B, A + B).
