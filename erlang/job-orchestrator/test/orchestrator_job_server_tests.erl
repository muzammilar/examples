-module(orchestrator_job_server_tests).

-include_lib("eunit/include/eunit.hrl").

node_of_test_() ->
    Here = atom_to_binary(node()),
    [
        ?_assertEqual({ok, node()}, orchestrator_job_server:node_of(<<"1f.", Here/binary>>)),
        ?_assertEqual({error, not_found}, orchestrator_job_server:node_of(<<"no-dot">>)),
        %% An unknown node name must not become a new atom.
        ?_assertEqual(
            {error, not_found},
            orchestrator_job_server:node_of(<<"1f.never_seen_before_node@nowhere">>)
        )
    ].
