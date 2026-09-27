-module(orchestrator_tcp_sup).
-moduledoc """
The listening socket and its connections.

`rest_for_one`: the listener hands its socket to acceptors under the
connection supervisor, which must be running first.
""".
-behaviour(supervisor).

-export([start_link/0]).
-export([init/1]).

-spec start_link() -> supervisor:startlink_ret().
start_link() ->
    supervisor:start_link(?MODULE, []).

-doc false.
init([]) ->
    Flags = #{strategy => rest_for_one, intensity => 5, period => 10},
    Children = [
        #{id => conns, start => {orchestrator_conn_sup, start_link, []}, type => supervisor},
        #{id => listener, start => {orchestrator_listener, start_link, []}}
    ],
    {ok, {Flags, Children}}.
