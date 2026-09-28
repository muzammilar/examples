-module(orchestrator_conn_sup).
-moduledoc """
Supervises client connections.

Connections are `temporary`: if one crashes, its client is gone anyway.
""".
-behaviour(supervisor).

-export([start_link/0, start_acceptor/1]).
-export([init/1]).

-spec start_link() -> supervisor:startlink_ret().
start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

-doc "Starts a process that waits for the next client on `ListenSocket`.".
-spec start_acceptor(gen_tcp:socket()) -> supervisor:startchild_ret().
start_acceptor(ListenSocket) ->
    supervisor:start_child(?MODULE, [ListenSocket]).

-doc false.
init([]) ->
    Flags = #{strategy => simple_one_for_one},
    Child = #{
        id => conn,
        start => {orchestrator_conn, start_link, []},
        restart => temporary
    },
    {ok, {Flags, [Child]}}.
