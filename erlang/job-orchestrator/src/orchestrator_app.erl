-module(orchestrator_app).
-moduledoc false.
-behaviour(application).

-export([start/2, stop/1]).

-spec start(application:start_type(), term()) -> supervisor:startlink_ret().
start(_Type, _Args) ->
    orchestrator_sup:start_link().

-spec stop(term()) -> ok.
stop(_State) ->
    ok.
