-module(orchestrator_protocol).
-moduledoc """
The line-based text protocol spoken over TCP. See the README for the
commands and replies. Arguments are capped (`MAX_FIB`, `MAX_SLEEP_MS`).
""".

-export([decode/1, encode/1]).
-export_type([key/0, request/0, reply/0]).

-define(MAX_FIB, 10_000).
-define(MAX_SLEEP_MS, 60_000).

-type key() :: binary() | undefined.
-type request() ::
    {run | submit, orchestrator_task:task(), key()}
    | {result, orchestrator_job_server:job_id()}
    | {owner, binary()}
    | status
    | nodes.
-type reply() ::
    orchestrator_job_server:result()
    | {status, orchestrator_job_server:stats()}
    | {nodes, [node()]}
    | {owner, node()}
    | {job, orchestrator_job_server:job_id()}
    | pending
    | {error, busy | bad_request | unavailable | not_found}.

-doc "Parses one request line.".
-spec decode(binary()) -> {ok, request()} | {error, bad_request}.
decode(Line) ->
    %% "\r\n" is one grapheme to the string module, so it's listed on its own.
    case string:lexemes(Line, [$\s, $\t, $\r, $\n, "\r\n"]) of
        [<<"status">>] -> {ok, status};
        [<<"nodes">>] -> {ok, nodes};
        [<<"owner">>, Key] -> {ok, {owner, Key}};
        [<<"result">>, Id] -> {ok, {result, Id}};
        [<<"submit">> | Job] -> detach(decode_job(Job));
        Words -> decode_job(Words)
    end.

detach({ok, {run, Task, Key}}) -> {ok, {submit, Task, Key}};
detach({error, _} = Error) -> Error.

decode_job(Words) ->
    {Job, Key} =
        case lists:last([<<>> | Words]) of
            <<"key=", Key0/binary>> when Key0 =/= <<>> -> {lists:droplast(Words), Key0};
            _ -> {Words, undefined}
        end,
    case Job of
        [<<"fib">>, N] -> bounded(N, ?MAX_FIB, fun(C) -> {run, {fib, C}, Key} end);
        [<<"sleep">>, Ms] -> bounded(Ms, ?MAX_SLEEP_MS, fun(C) -> {run, {sleep, C}, Key} end);
        [<<"crash">>] -> {ok, {run, crash, Key}};
        _ -> {error, bad_request}
    end.

-doc "Renders a reply as one newline-terminated line.".
-spec encode(reply()) -> iodata().
encode({ok, ok}) ->
    <<"ok\n">>;
encode({ok, Result}) ->
    io_lib:format("ok ~p\n", [Result]);
encode({status, #{queued := Q, idle := I, busy := B}}) ->
    io_lib:format("ok queued=~b idle=~b busy=~b\n", [Q, I, B]);
encode({nodes, Nodes}) ->
    [<<"ok">>, [[$\s, atom_to_binary(N)] || N <- Nodes], $\n];
encode({owner, Node}) ->
    [<<"ok ">>, atom_to_binary(Node), $\n];
encode({job, Id}) ->
    [<<"ok job ">>, Id, $\n];
encode(pending) ->
    <<"pending\n">>;
encode({error, {crashed, _Reason}}) ->
    <<"error crashed\n">>;
encode({error, Reason}) ->
    io_lib:format("error ~s\n", [Reason]).

bounded(Bin, Max, Wrap) ->
    try binary_to_integer(Bin) of
        N when N >= 0, N =< Max -> {ok, Wrap(N)};
        _ -> {error, bad_request}
    catch
        error:badarg -> {error, bad_request}
    end.
