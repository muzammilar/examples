-module(orchestrator_pool_sup).
-moduledoc """
The job server and its workers.

`rest_for_one`: workers register with the job server when they start, and
must register again with a new one after it restarts.
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
        #{id => job_server, start => {orchestrator_job_server, start_link, []}},
        #{id => workers, start => {orchestrator_worker_sup, start_link, []}, type => supervisor}
    ],
    {ok, {Flags, Children}}.
