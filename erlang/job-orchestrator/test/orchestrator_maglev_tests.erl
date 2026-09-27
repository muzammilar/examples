-module(orchestrator_maglev_tests).

-include_lib("eunit/include/eunit.hrl").

members(N) ->
    [list_to_atom("node" ++ integer_to_list(I)) || I <- lists:seq(1, N)].

owners(Table, Keys) ->
    [orchestrator_maglev:lookup(K, Table) || K <- Keys].

keys() ->
    lists:seq(1, 10_000).

every_member_owns_some_keys_test() ->
    Table = orchestrator_maglev:new(members(5)),
    ?assertEqual(lists:usort(members(5)), lists:usort(owners(Table, keys()))).

shares_are_nearly_equal_test_() ->
    [?_test(shares_are_nearly_equal(N)) || N <- [3, 4, 5]].

shares_are_nearly_equal(N) ->
    Table = orchestrator_maglev:new(members(N)),
    Counts = maps:values(
        lists:foldl(
            fun(Node, Acc) -> maps:update_with(Node, fun(C) -> C + 1 end, 1, Acc) end,
            #{},
            owners(Table, keys())
        )
    ),
    Fair = 10_000 / N,
    %% Maglev keeps slot counts within one of each other; key counts vary a
    %% little more with the hash.
    [?assert(abs(C - Fair) < Fair * 0.1) || C <- Counts].

member_order_does_not_matter_test() ->
    Nodes = members(5),
    Table = orchestrator_maglev:new(Nodes),
    ?assertEqual(Table, orchestrator_maglev:new(lists:reverse(Nodes))).

losing_a_node_moves_few_other_keys_test() ->
    Before = owners(orchestrator_maglev:new(members(5)), keys()),
    After = owners(orchestrator_maglev:new(members(5) -- [node3]), keys()),
    Pairs = lists:zip(Before, After),

    %% Every key node3 owned has to move, to a surviving node.
    [?assertNotEqual(node3, New) || {node3, New} <- Pairs],
    %% Of the rest, only a few percent move; modulo hashing would move ~80%.
    Moved = length([ok || {Old, New} <- Pairs, Old =/= node3, Old =/= New]),
    Kept = length([ok || {Old, _} <- Pairs, Old =/= node3]),
    ?assert(Moved / Kept < 0.1).

adding_a_node_takes_keys_from_every_other_test() ->
    Before = owners(orchestrator_maglev:new(members(4)), keys()),
    After = owners(orchestrator_maglev:new(members(5)), keys()),
    Moved = [Old || {Old, New} <- lists:zip(Before, After), Old =/= New],
    %% Roughly a fifth of the keys move, taken from all four old nodes.
    ?assert(length(Moved) < 10_000 * 0.3),
    ?assertEqual(lists:usort(members(4)), lists:usort(Moved)).

a_single_member_owns_everything_test() ->
    Table = orchestrator_maglev:new([only]),
    ?assertEqual([only], lists:usort(owners(Table, keys()))).
