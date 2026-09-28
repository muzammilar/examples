#!/usr/bin/env escript
%% Load test for the job orchestrator. Many clients connect over TCP and each
%% sends jobs one at a time; the report shows throughput and latency
%% percentiles. `make bench` runs it against the server in compose.yaml.
-module(bench).
-mode(compile).

-export([main/1]).

main(Args) ->
    argparse:run(Args, cli(), #{progname => "bench"}).

cli() ->
    Count = {integer, [{min, 1}]},
    #{
        arguments => [
            #{name => host, long => "-host", default => "localhost"},
            #{
                name => port,
                long => "-port",
                type => {integer, [{min, 1}, {max, 65535}]},
                default => 5555
            },
            #{name => clients, long => "-clients", type => Count, default => 32},
            #{name => requests, long => "-requests", type => Count, default => 5000},
            #{name => task, long => "-task", default => "fib 20", help => "request line to send"}
        ],
        handler => fun run/1
    }.

run(#{host := Host, port := Port, clients := Clients, requests := Requests, task := Task}) ->
    io:format("~b x \"~s\" from ~b clients~n", [Requests, Task, Clients]),
    Line = iolist_to_binary([Task, $\n]),
    Started = erlang:monotonic_time(),
    Monitors = [
        spawn_monitor(fun() ->
            exit({done, client(Host, Port, Line, share(Requests, Clients, N))})
        end)
     || N <- lists:seq(1, Clients)
    ],
    Results = [collect(Mon) || {_, Mon} <- Monitors],
    Elapsed = micros(erlang:monotonic_time() - Started),
    Latencies = lists:sort(lists:append([L || {L, _} <- Results])),
    Refused = lists:foldl(
        fun({_, Counts}, Acc) -> maps:merge_with(fun(_, A, B) -> A + B end, Acc, Counts) end,
        #{},
        Results
    ),
    report(Latencies, Refused, Elapsed).

%% A client that crashed, e.g. because the server went away, counts as one
%% refusal and the run carries on.
collect(Mon) ->
    receive
        {'DOWN', Mon, process, _, {done, Result}} ->
            Result;
        {'DOWN', Mon, process, _, Reason} ->
            {[], #{iolist_to_binary(io_lib:format("~0p", [Reason])) => 1}}
    after 120_000 -> {[], #{<<"client timed out">> => 1}}
    end.

%% This client's share of the requests, spreading any remainder.
share(Requests, Clients, N) when N =< Requests rem Clients -> Requests div Clients + 1;
share(Requests, Clients, _N) -> Requests div Clients.

client(Host, Port, Line, Count) ->
    case gen_tcp:connect(Host, Port, [binary, {packet, line}, {active, false}], 5000) of
        {ok, Socket} ->
            Result = loop(Socket, Line, Count, [], #{}),
            ok = gen_tcp:close(Socket),
            Result;
        {error, Reason} ->
            {[], #{iolist_to_binary(io_lib:format("connect: ~p", [Reason])) => Count}}
    end.

%% Returns the latency of each successful request, in microseconds, and a
%% count of every other reply, e.g. `#{<<"error busy">> => 12}`.
loop(_Socket, _Line, 0, Latencies, Refused) ->
    {Latencies, Refused};
loop(Socket, Line, Count, Latencies, Refused) ->
    Sent = erlang:monotonic_time(),
    ok = gen_tcp:send(Socket, Line),
    {ok, Reply} = gen_tcp:recv(Socket, 0, 60_000),
    Took = micros(erlang:monotonic_time() - Sent),
    case string:trim(Reply) of
        <<"ok", _/binary>> ->
            loop(Socket, Line, Count - 1, [Took | Latencies], Refused);
        Other ->
            loop(
                Socket,
                Line,
                Count - 1,
                Latencies,
                maps:update_with(Other, fun(N) -> N + 1 end, 1, Refused)
            )
    end.

report([], Refused, _Elapsed) ->
    io:format("no request succeeded: ~p~n", [Refused]),
    halt(1);
report(Latencies, Refused, Elapsed) ->
    Ms = fun(Us) -> io_lib:format("~.1fms", [Us / 1000]) end,
    [io:format("~b x \"~s\"~n", [N, Reply]) || Reply := N <- Refused],
    io:format(
        "~b ok   ~b jobs/s   p50 ~s   p95 ~s   p99 ~s   max ~s~n",
        [
            length(Latencies),
            round(length(Latencies) / (Elapsed / 1_000_000)),
            Ms(percentile(Latencies, 50)),
            Ms(percentile(Latencies, 95)),
            Ms(percentile(Latencies, 99)),
            Ms(lists:last(Latencies))
        ]
    ).

%% Nearest-rank percentile of a sorted, non-empty list.
percentile(Sorted, Percent) ->
    Rank = max(1, ceil(length(Sorted) * Percent / 100)),
    lists:nth(Rank, Sorted).

micros(Native) ->
    erlang:convert_time_unit(Native, native, microsecond).
