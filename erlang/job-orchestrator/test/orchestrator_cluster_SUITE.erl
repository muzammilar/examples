-module(orchestrator_cluster_SUITE).

-include_lib("common_test/include/ct.hrl").
-include_lib("stdlib/include/assert.hrl").

-export([all/0, init_per_suite/1, end_per_suite/1, init_per_testcase/2, end_per_testcase/2]).
-export([
    every_node_sees_the_whole_cluster/1,
    nodes_agree_on_every_owner/1,
    keyed_jobs_run_on_the_owner/1,
    losing_a_node_moves_few_other_keys/1,
    detached_jobs_can_be_collected_from_any_node/1
]).

-define(NODES, 3).

all() ->
    [
        every_node_sees_the_whole_cluster,
        nodes_agree_on_every_owner,
        keyed_jobs_run_on_the_owner,
        losing_a_node_moves_few_other_keys,
        detached_jobs_can_be_collected_from_any_node
    ].

%% Peer nodes can only be started from a distributed node.
init_per_suite(Config) ->
    case is_alive() of
        true ->
            [{started_distribution, false} | Config];
        false ->
            [] = os:cmd("epmd -daemon"),
            {ok, _} = net_kernel:start(ct, #{name_domain => shortnames}),
            [{started_distribution, true} | Config]
    end.

end_per_suite(Config) ->
    case ?config(started_distribution, Config) of
        true -> ok = net_kernel:stop();
        false -> ok
    end.

%% Each test gets a fresh cluster of peer nodes, all running the app.
init_per_testcase(_Case, Config) ->
    [_, Host] = string:split(atom_to_list(node()), "@"),
    Names = [peer:random_name(?MODULE) || _ <- lists:seq(1, ?NODES)],
    Nodes = [list_to_atom(Name ++ "@" ++ Host) || Name <- Names],
    Peers = [start_node(Name, Nodes) || Name <- Names],
    [wait_for_members(Node, Nodes) || Node <- Nodes],
    [{peers, Peers}, {nodes, Nodes} | Config].

end_per_testcase(_Case, Config) ->
    [stop_node(Peer) || {Peer, _Node} <- ?config(peers, Config)],
    ok.

%%% Tests

every_node_sees_the_whole_cluster(Config) ->
    Nodes = ?config(nodes, Config),
    [
        ?assertEqual(lists:sort(Nodes), erpc:call(N, orchestrator_cluster, members, []))
     || N <- Nodes
    ].

nodes_agree_on_every_owner(Config) ->
    [First | Rest] = Nodes = ?config(nodes, Config),
    Expected = owners(First, keys()),
    [?assertEqual(Expected, owners(N, keys())) || N <- Rest],
    %% And every node owns some of them.
    ?assertEqual(lists:sort(Nodes), lists:usort(Expected)).

keyed_jobs_run_on_the_owner(Config) ->
    [Entry | _] = Nodes = ?config(nodes, Config),
    %% A key owned by some other node than the one the client talks to.
    Key = hd([K || K <- keys(), owner(Entry, K) =/= Entry]),
    Owner = owner(Entry, Key),

    Socket = connect(Entry),
    ?assertEqual(
        iolist_to_binary(["ok ", atom_to_list(Owner), "\n"]),
        orchestrator_test_client:request(Socket, ["owner ", Key])
    ),
    ok = gen_tcp:send(Socket, ["sleep 500 key=", Key, "\n"]),
    orchestrator_test_client:eventually(fun() -> busy(Owner) =:= 1 end),
    [?assertEqual(0, busy(N)) || N <- Nodes, N =/= Owner],
    ?assertEqual(<<"ok\n">>, orchestrator_test_client:recv(Socket)).

losing_a_node_moves_few_other_keys(Config) ->
    [Survivor, _, Lost] = ?config(nodes, Config),
    {LostPeer, Lost} = lists:keyfind(Lost, 2, ?config(peers, Config)),
    Before = lists:zip(keys(), owners(Survivor, keys())),

    peer:stop(LostPeer),
    orchestrator_test_client:eventually(fun() ->
        not lists:member(Lost, erpc:call(Survivor, orchestrator_cluster, members, []))
    end),
    After = lists:zip(keys(), owners(Survivor, keys())),

    [?assertNotEqual(Lost, New) || {_, New} <- After],
    %% Maglev keeps most of the other keys where they were; modulo hashing
    %% would move about half of them going from three nodes to two.
    Pairs = [{Old, New} || {{_, Old}, {_, New}} <- lists:zip(Before, After), Old =/= Lost],
    Moved = length([ok || {Old, New} <- Pairs, Old =/= New]),
    ?assert(Moved / length(Pairs) < 0.1, #{moved => Moved, kept => length(Pairs)}),
    %% And jobs for the lost node's keys still run, on their new owner.
    LostKey = hd([K || {K, Owner} <- Before, Owner =:= Lost]),
    ?assertEqual(
        <<"ok 55\n">>, orchestrator_test_client:request(connect(Survivor), ["fib 10 key=", LostKey])
    ).

detached_jobs_can_be_collected_from_any_node(Config) ->
    [Entry, Other, Third] = ?config(nodes, Config),
    Key = hd([K || K <- keys(), owner(Entry, K) =:= Third]),

    Id = orchestrator_test_client:job_id(
        orchestrator_test_client:request(connect(Entry), ["submit sleep 200 key=", Key])
    ),
    ?assertEqual({ok, Third}, erpc:call(Entry, orchestrator_job_server, node_of, [Id])),

    %% Reconnect to a different node, as a load balancer might.
    Again = connect(Other),
    orchestrator_test_client:eventually(fun() ->
        orchestrator_test_client:request(Again, ["result ", Id]) =:= <<"ok\n">>
    end).

%%% Helpers

start_node(Name, Nodes) ->
    Ebin = filename:dirname(code:which(orchestrator_cluster)),
    {ok, Peer, Node} = ?CT_PEER(#{name => Name, args => ["-pa", Ebin]}),
    ok = erpc:call(Node, application, load, [orchestrator]),
    Peers = string:join([atom_to_list(N) || N <- Nodes], ","),
    Env = #{port => 0, acceptors => 2, workers => 2, peers => Peers},
    maps:foreach(
        fun(K, V) -> ok = erpc:call(Node, application, set_env, [orchestrator, K, V]) end, Env
    ),
    {ok, _} = erpc:call(Node, application, ensure_all_started, [orchestrator]),
    {Peer, Node}.

%% Some tests stop a node themselves.
stop_node(Peer) ->
    try
        peer:stop(Peer)
    catch
        exit:noproc -> ok
    end.

wait_for_members(Node, Nodes) ->
    Expected = lists:sort(Nodes),
    orchestrator_test_client:eventually(fun() ->
        erpc:call(Node, orchestrator_cluster, members, []) =:= Expected
    end).

keys() ->
    [integer_to_binary(I) || I <- lists:seq(1, 300)].

owners(Node, Keys) ->
    [owner(Node, K) || K <- Keys].

%% The node that `Node` thinks owns `Key`.
owner(Node, Key) ->
    {ok, Server} = erpc:call(Node, orchestrator_cluster, owner, [Key]),
    node(Server).

busy(Node) ->
    maps:get(busy, erpc:call(Node, orchestrator_job_server, stats, [])).

connect(Node) ->
    orchestrator_test_client:connect(erpc:call(Node, orchestrator_listener, port, [])).
