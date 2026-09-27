-module(orchestrator_cluster).
-moduledoc """
Joins this node to its peers and decides which node owns each job key.

Every job server joins the `pg` group `job_servers`. This process watches
that group; whenever a member joins or leaves, it rebuilds a Maglev table
(see `orchestrator_maglev`) over the members' nodes and publishes it with
`persistent_term`. Routing a key is then a hash and a tuple lookup.

Peers come from the `peers` setting. Connecting is retried every second,
which lets nodes start in any order and rejoin after a restart.

Limitation: no notion of a network partition. Each side of a split routes
keys among the nodes it can see, and both can own the same key.
""".
-behaviour(gen_server).

-export([start_link/0, owner/1, members/0]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2]).

-define(GROUP, job_servers).
-define(ROUTES, {?MODULE, routes}).
-define(CONNECT_EVERY_MS, 1000).

-record(state, {
    peers :: [node()],
    monitor :: reference(),
    members :: #{pid() => node()}
}).

-spec start_link() -> gen_server:start_ret().
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

-doc "The job server that owns `Key`, on whichever node that is.".
-spec owner(term()) -> {ok, pid()} | {error, unavailable}.
owner(Key) ->
    case persistent_term:get(?ROUTES, none) of
        none -> {error, unavailable};
        {Table, Servers} -> {ok, maps:get(orchestrator_maglev:lookup(Key, Table), Servers)}
    end.

-doc "Nodes with a job server in the cluster, sorted.".
-spec members() -> [node()].
members() ->
    case persistent_term:get(?ROUTES, none) of
        none -> [];
        {_Table, Servers} -> lists:sort(maps:keys(Servers))
    end.

-doc false.
init([]) ->
    {Mon, Pids} = pg:monitor(?GROUP),
    self() ! connect,
    State = #state{peers = peers(), monitor = Mon, members = #{}},
    {ok, publish(join(Pids, State))}.

-doc false.
handle_call(Request, From, State) ->
    logger:warning("unexpected call from ~p: ~p", [From, Request]),
    {reply, {error, unknown_request}, State}.

-doc false.
handle_cast(Msg, State) ->
    logger:warning("unexpected cast: ~p", [Msg]),
    {noreply, State}.

-doc false.
handle_info(connect, #state{peers = Peers} = State) ->
    %% Only a distributed node can connect; a plain one runs alone.
    case is_alive() of
        true -> lists:foreach(fun net_kernel:connect_node/1, Peers -- [node() | nodes()]);
        false -> ok
    end,
    erlang:send_after(?CONNECT_EVERY_MS, self(), connect),
    {noreply, State};
handle_info({Mon, join, ?GROUP, Pids}, #state{monitor = Mon} = State) ->
    {noreply, publish(join(Pids, State))};
handle_info({Mon, leave, ?GROUP, Pids}, #state{monitor = Mon} = State) ->
    {noreply, publish(State#state{members = maps:without(Pids, State#state.members)})};
handle_info(Msg, State) ->
    logger:warning("unexpected message: ~p", [Msg]),
    {noreply, State}.

join(Pids, #state{members = Members} = State) ->
    State#state{members = maps:merge(Members, maps:from_list([{P, node(P)} || P <- Pids]))}.

%% Rebuilt only when membership changes, which is rare: updating a
%% persistent term makes every process that read it copy it once.
publish(#state{members = Members} = State) when map_size(Members) =:= 0 ->
    _ = persistent_term:erase(?ROUTES),
    State;
publish(#state{members = Members} = State) ->
    %% One job server per node; during a restart the old and new ones may
    %% briefly both be members, and either will do.
    Servers = maps:from_list([{Node, Pid} || Pid := Node <- Members]),
    Table = orchestrator_maglev:new(maps:keys(Servers)),
    persistent_term:put(?ROUTES, {Table, Servers}),
    logger:notice("cluster members: ~p", [lists:sort(maps:keys(Servers))]),
    State.

%% A comma-separated string of node names, as it arrives from the environment.
peers() ->
    {ok, Peers} = application:get_env(orchestrator, peers),
    [list_to_atom(P) || P <- string:lexemes(Peers, ", ")].
