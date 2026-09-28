-module(orchestrator_test_client).
-moduledoc "Helpers the test suites share: a line-protocol client, and polling.".

-export([connect/1, connect/2, request/2, recv/1, job_id/1, eventually/1]).

%% How long `eventually/1` waits: 250 tries, 20ms apart.
-define(TRIES, 250).
-define(PAUSE_MS, 20).

-doc #{equiv => connect(Port, [])}.
-spec connect(inet:port_number()) -> gen_tcp:socket().
connect(Port) ->
    connect(Port, []).

-doc "Connects to a server on this host, with extra socket options.".
-spec connect(inet:port_number(), [gen_tcp:connect_option()]) -> gen_tcp:socket().
connect(Port, Extra) ->
    Opts = [binary, {packet, line}, {active, false} | Extra],
    {ok, Socket} = gen_tcp:connect({127, 0, 0, 1}, Port, Opts),
    Socket.

-doc "Sends one request, adding the newline if it's missing, and returns the reply.".
-spec request(gen_tcp:socket(), iodata()) -> binary().
request(Socket, Line) ->
    ok = gen_tcp:send(Socket, [string:trim(iolist_to_binary(Line), trailing, "\n"), $\n]),
    recv(Socket).

-doc "The next reply line.".
-spec recv(gen_tcp:socket()) -> binary().
recv(Socket) ->
    {ok, Reply} = gen_tcp:recv(Socket, 0, 5000),
    Reply.

-doc "The job ID in a reply to `submit`.".
-spec job_id(binary()) -> binary().
job_id(<<"ok job ", Id/binary>>) ->
    string:trim(Id).

-doc "Polls `Condition` until it holds, failing the test after five seconds.".
-spec eventually(fun(() -> boolean())) -> ok.
eventually(Condition) ->
    eventually(Condition, ?TRIES).

eventually(Condition, 0) ->
    ct:fail({condition_never_held, Condition});
eventually(Condition, Tries) ->
    case Condition() of
        true ->
            ok;
        false ->
            timer:sleep(?PAUSE_MS),
            eventually(Condition, Tries - 1)
    end.
