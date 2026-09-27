-module(orchestrator_task_tests).

-include_lib("eunit/include/eunit.hrl").

fib_test_() ->
    [
        ?_assertEqual(Expected, orchestrator_task:run({fib, N}))
     || {N, Expected} <- [{0, 0}, {1, 1}, {2, 1}, {10, 55}, {30, 832040}]
    ].

crash_raises_test() ->
    ?assertError(crash_requested, orchestrator_task:run(crash)).
