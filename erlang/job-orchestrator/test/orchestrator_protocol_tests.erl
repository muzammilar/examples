-module(orchestrator_protocol_tests).

-include_lib("eunit/include/eunit.hrl").

decode_test_() ->
    Cases = [
        {<<"fib 30\n">>, {ok, {run, {fib, 30}, undefined}}},
        {<<"sleep 0\r\n">>, {ok, {run, {sleep, 0}, undefined}}},
        {<<"  crash  \n">>, {ok, {run, crash, undefined}}},
        {<<"status\n">>, {ok, status}},
        {<<"nodes\n">>, {ok, nodes}},
        {<<"owner user42\n">>, {ok, {owner, <<"user42">>}}},
        {<<"fib 3 key=user42\n">>, {ok, {run, {fib, 3}, <<"user42">>}}},
        {<<"crash key=k\n">>, {ok, {run, crash, <<"k">>}}},
        {<<"fib 3 key=\n">>, {error, bad_request}},
        {<<"key=k\n">>, {error, bad_request}},
        {<<"owner\n">>, {error, bad_request}},
        {<<"submit fib 3 key=k\n">>, {ok, {submit, {fib, 3}, <<"k">>}}},
        {<<"submit sleep 5\n">>, {ok, {submit, {sleep, 5}, undefined}}},
        {<<"submit\n">>, {error, bad_request}},
        {<<"submit status\n">>, {error, bad_request}},
        {<<"result 1f.a@host\n">>, {ok, {result, <<"1f.a@host">>}}},
        {<<"result\n">>, {error, bad_request}},
        {<<"fib\n">>, {error, bad_request}},
        {<<"fib -1\n">>, {error, bad_request}},
        {<<"fib ten\n">>, {error, bad_request}},
        {<<"fib 1 2\n">>, {error, bad_request}},
        {<<"FIB 3\n">>, {error, bad_request}},
        {<<"fib 10000\n">>, {ok, {run, {fib, 10000}, undefined}}},
        {<<"fib 10001\n">>, {error, bad_request}},
        {<<"sleep 60001\n">>, {error, bad_request}},
        {<<"\n">>, {error, bad_request}}
    ],
    [
        {Line, ?_assertEqual(Expected, orchestrator_protocol:decode(Line))}
     || {Line, Expected} <- Cases
    ].

encode_test_() ->
    Cases = [
        {{ok, ok}, <<"ok\n">>},
        {{ok, 832040}, <<"ok 832040\n">>},
        {{status, #{queued => 1, idle => 2, busy => 3}}, <<"ok queued=1 idle=2 busy=3\n">>},
        {{error, busy}, <<"error busy\n">>},
        {{nodes, [node_a(), node_b()]}, <<"ok a@host b@host\n">>},
        {{nodes, []}, <<"ok\n">>},
        {{owner, node_a()}, <<"ok a@host\n">>},
        {{job, <<"1f.a@host">>}, <<"ok job 1f.a@host\n">>},
        {pending, <<"pending\n">>},
        {{error, not_found}, <<"error not_found\n">>},
        {{error, timeout}, <<"error timeout\n">>},
        {{error, bad_request}, <<"error bad_request\n">>},
        {{error, unavailable}, <<"error unavailable\n">>},
        {{error, {crashed, {error, crash_requested, []}}}, <<"error crashed\n">>}
    ],
    [
        ?_assertEqual(Expected, iolist_to_binary(orchestrator_protocol:encode(Reply)))
     || {Reply, Expected} <- Cases
    ].

%% elvis's atom_naming_convention rejects a quoted 'a@host' literal.
node_a() -> list_to_atom("a@host").
node_b() -> list_to_atom("b@host").
