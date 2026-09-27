-module(orchestrator_listener).
-moduledoc """
Owns the listening socket and seeds the pool of acceptors.

Accepting happens in the connection processes themselves: each one waits
in `gen_tcp:accept/2`, and once it has a client it starts a replacement
acceptor before serving. A slow client never holds up new connections.
""".
-behaviour(gen_server).

-export([start_link/0, port/0]).
-export([init/1, handle_call/3, handle_cast/2, handle_continue/2]).

%% Limitation: no authentication and no cap on connections. Each holds a
%% process and a file descriptor until it closes or goes idle.

-spec start_link() -> gen_server:start_ret().
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

-doc "The port actually bound. Useful when configured with port 0.".
-spec port() -> inet:port_number().
port() ->
    gen_server:call(?MODULE, port).

-doc false.
init([]) ->
    {ok, Port} = application:get_env(orchestrator, port),
    Opts = [
        binary,
        %% Limitation: a line longer than the socket's buffer arrives in
        %% pieces, each treated as a request.
        {packet, line},
        {active, false},
        {reuseaddr, true},
        {backlog, 1024},
        %% Keep the socket writable after the client half-closes, and report
        %% a reset as an error: connections tell "done sending" from "gone".
        {exit_on_close, false},
        {show_econnreset, true},
        %% A client that stops reading can't wedge its connection process.
        {send_timeout, 5000},
        {send_timeout_close, true}
    ],
    case gen_tcp:listen(Port, Opts) of
        {ok, Socket} ->
            {ok, Socket, {continue, start_acceptors}};
        {error, Reason} ->
            {stop, {listen_failed, Port, Reason}}
    end.

-doc false.
handle_continue(start_acceptors, Socket) ->
    {ok, Count} = application:get_env(orchestrator, acceptors),
    {ok, Port} = inet:port(Socket),
    logger:notice("listening on port ~b with ~b acceptors", [Port, Count]),
    lists:foreach(
        fun(_) -> {ok, _} = orchestrator_conn_sup:start_acceptor(Socket) end,
        lists:seq(1, Count)
    ),
    {noreply, Socket}.

-doc false.
handle_call(port, _From, Socket) ->
    {ok, Port} = inet:port(Socket),
    {reply, Port, Socket}.

-doc false.
handle_cast(_Msg, Socket) ->
    {noreply, Socket}.
