-module(orchestrator_worker_sup).
-moduledoc "Keeps a fixed-size pool of workers alive.".
-behaviour(supervisor).

-export([start_link/0]).
-export([init/1]).

-spec start_link() -> supervisor:startlink_ret().
start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

-doc false.
init([]) ->
    {ok, Count} = application:get_env(orchestrator, workers),
    Flags = #{strategy => one_for_one, intensity => 5, period => 10},
    Children = [
        #{id => {worker, N}, start => {orchestrator_worker, start_link, []}}
     || N <- lists:seq(1, Count)
    ],
    {ok, {Flags, Children}}.
