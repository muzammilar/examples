-module(orchestrator_sup).
-moduledoc """
Top-level supervisor. The worker pool and the TCP front end are independent
subtrees: connections reach the job server by name and monitor it.
""".
-behaviour(supervisor).

-export([start_link/0]).
-export([init/1]).

-spec start_link() -> supervisor:startlink_ret().
start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

-doc false.
init([]) ->
    Flags = #{strategy => one_for_one, intensity => 5, period => 10},
    Children = [
        %% The default `pg` scope, which job servers join and the cluster
        %% process watches.
        #{id => pg, start => {pg, start_link, []}},
        #{id => cluster, start => {orchestrator_cluster, start_link, []}},
        #{id => pool, start => {orchestrator_pool_sup, start_link, []}, type => supervisor},
        #{id => tcp, start => {orchestrator_tcp_sup, start_link, []}, type => supervisor}
    ],
    {ok, {Flags, Children}}.
