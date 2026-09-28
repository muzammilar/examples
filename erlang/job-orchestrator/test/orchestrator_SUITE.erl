-module(orchestrator_SUITE).

-include_lib("common_test/include/ct.hrl").
-include_lib("stdlib/include/assert.hrl").

-export([all/0, groups/0, init_per_testcase/2, end_per_testcase/2]).
-export([
    runs_a_job/1,
    runs_jobs_in_parallel/1,
    queues_jobs_beyond_the_pool/1,
    rejects_work_when_the_queue_is_full/1,
    no_queue_still_uses_idle_workers/1,
    crashing_task_is_retried_then_fails/1,
    slow_task_times_out/1,
    killed_worker_is_replaced_and_its_job_retried/1,
    caller_exit_cancels_its_jobs/1,
    run_timeout_cancels_the_job/1,
    cancel_is_idempotent/1,
    stray_messages_are_ignored/1,
    detached_results_expire/1,
    unknown_job_ids_are_not_found/1,
    run_reports_a_job_server_restart/1,
    round_trip/1,
    rejects_bad_requests/1,
    answers_pipelined_requests_in_order/1,
    serves_many_clients/1,
    half_close_still_gets_replies/1,
    reset_cancels_the_running_job/1,
    client_is_told_of_a_job_server_restart/1,
    idle_connections_are_closed/1,
    detached_job_survives_a_disconnect/1,
    acceptors_answer_system_messages/1
]).

-define(WORKERS, 2).
-define(MAX_QUEUE, 2).
-define(LONG, {sleep, 10_000}).

all() ->
    [{group, jobs}, {group, tcp}].

groups() ->
    [
        {jobs, [], [
            runs_a_job,
            runs_jobs_in_parallel,
            queues_jobs_beyond_the_pool,
            rejects_work_when_the_queue_is_full,
            no_queue_still_uses_idle_workers,
            crashing_task_is_retried_then_fails,
            slow_task_times_out,
            killed_worker_is_replaced_and_its_job_retried,
            caller_exit_cancels_its_jobs,
            run_timeout_cancels_the_job,
            cancel_is_idempotent,
            stray_messages_are_ignored,
            detached_results_expire,
            unknown_job_ids_are_not_found,
            run_reports_a_job_server_restart
        ]},
        {tcp, [], [
            round_trip,
            rejects_bad_requests,
            answers_pipelined_requests_in_order,
            serves_many_clients,
            half_close_still_gets_replies,
            reset_cancels_the_running_job,
            client_is_told_of_a_job_server_restart,
            idle_connections_are_closed,
            detached_job_survives_a_disconnect,
            acceptors_answer_system_messages
        ]}
    ].

%% Each test gets a freshly started application.
init_per_testcase(Case, Config) ->
    %% Load first: loading resets the env to the .app defaults.
    ok = application:load(orchestrator),
    Env = maps:merge(
        #{
            port => 0,
            acceptors => 2,
            workers => ?WORKERS,
            max_queue => ?MAX_QUEUE,
            max_attempts => 3,
            job_timeout => 20_000,
            idle_timeout => 60_000
        },
        env_overrides(Case)
    ),
    maps:foreach(fun(K, V) -> application:set_env(orchestrator, K, V) end, Env),
    {ok, _} = application:ensure_all_started(orchestrator),
    wait_until_idle(),
    Config.

end_per_testcase(_Case, _Config) ->
    ok = application:stop(orchestrator),
    ok = application:unload(orchestrator).

env_overrides(slow_task_times_out) -> #{job_timeout => 100};
env_overrides(no_queue_still_uses_idle_workers) -> #{max_queue => 0};
env_overrides(idle_connections_are_closed) -> #{idle_timeout => 200};
env_overrides(detached_results_expire) -> #{result_ttl => 100};
env_overrides(_) -> #{}.

%%% Job server

runs_a_job(_Config) ->
    ?assertEqual({ok, 832040}, orchestrator_job_server:run({fib, 30})).

runs_jobs_in_parallel(_Config) ->
    Refs = [submit({sleep, 200}) || _ <- lists:seq(1, ?WORKERS)],
    wait_for_stats(#{busy => ?WORKERS, queued => 0, idle => 0}),
    ?assertEqual(lists:duplicate(?WORKERS, {ok, ok}), [await(R) || R <- Refs]).

queues_jobs_beyond_the_pool(_Config) ->
    Tasks = [{fib, N} || N <- lists:seq(1, ?WORKERS + ?MAX_QUEUE)],
    Refs = [submit(T) || T <- Tasks],
    ?assertEqual([{ok, orchestrator_task:run(T)} || T <- Tasks], [await(R) || R <- Refs]).

rejects_work_when_the_queue_is_full(_Config) ->
    Refs = [submit(?LONG) || _ <- lists:seq(1, ?WORKERS + ?MAX_QUEUE)],
    wait_for_stats(#{busy => ?WORKERS, queued => ?MAX_QUEUE, idle => 0}),

    ?assertEqual({error, busy}, orchestrator_job_server:submit({fib, 1})),
    [ok = orchestrator_job_server:cancel(R) || R <- Refs],
    wait_until_idle().

no_queue_still_uses_idle_workers(_Config) ->
    ?assertEqual({ok, 55}, orchestrator_job_server:run({fib, 10})),
    Refs = [submit(?LONG) || _ <- lists:seq(1, ?WORKERS)],
    wait_for_stats(#{busy => ?WORKERS}),
    ?assertEqual({error, busy}, orchestrator_job_server:submit({fib, 1})),
    [ok = orchestrator_job_server:cancel(R) || R <- Refs].

crashing_task_is_retried_then_fails(_Config) ->
    Workers = worker_pids(),
    ?assertMatch(
        {error, {crashed, {error, crash_requested, _Stack}}}, orchestrator_job_server:run(crash)
    ),
    %% The task crashed, not the worker: the pool is untouched.
    ?assertEqual(Workers, worker_pids()),
    ?assertEqual({ok, 55}, orchestrator_job_server:run({fib, 10})).

slow_task_times_out(_Config) ->
    ?assertEqual({error, timeout}, orchestrator_job_server:run(?LONG)),
    ?assertEqual({ok, 1}, orchestrator_job_server:run({fib, 1})).

killed_worker_is_replaced_and_its_job_retried(_Config) ->
    Ref = submit({sleep, 200}),
    wait_for_stats(#{busy => 1}),
    Before = worker_pids(),

    [exit(W, kill) || W <- Before],

    ?assertEqual({ok, ok}, await(Ref)),
    wait_until_idle(),
    After = worker_pids(),
    ?assertEqual(?WORKERS, length(After)),
    ?assertEqual([], [W || W <- After, lists:member(W, Before)]).

caller_exit_cancels_its_jobs(_Config) ->
    Parent = self(),
    {Caller, Mon} = spawn_monitor(fun() ->
        [submit(?LONG) || _ <- lists:seq(1, ?WORKERS + ?MAX_QUEUE)],
        Parent ! submitted
    end),
    receive
        submitted -> ok
    after 5000 -> ct:fail(caller_never_submitted)
    end,
    receive
        {'DOWN', Mon, process, Caller, normal} -> ok
    after 5000 -> ct:fail(caller_never_exited)
    end,
    wait_until_idle().

run_timeout_cancels_the_job(_Config) ->
    ?assertEqual({error, timeout}, orchestrator_job_server:run(?LONG, 50)),
    wait_until_idle(),
    receive
        {job_done, _, _} = Stray -> ct:fail({stray_result, Stray})
    after 0 -> ok
    end.

cancel_is_idempotent(_Config) ->
    Ref = submit(?LONG),
    wait_for_stats(#{busy => 1}),
    ok = orchestrator_job_server:cancel(Ref),
    ok = orchestrator_job_server:cancel(Ref),
    wait_until_idle(),
    receive
        {job_done, Ref, _} = Late -> ct:fail({result_after_cancel, Late})
    after 100 -> ok
    end.

stray_messages_are_ignored(_Config) ->
    Server = whereis(orchestrator_job_server),
    [Worker | _] = worker_pids(),
    %% A result for a job the worker already gave up on, as happens when a
    %% task finishes just as it is cancelled or times out.
    Worker ! {task_result, make_ref(), {ok, 1}},
    ?assertEqual({error, unknown_request}, gen_server:call(Server, nonsense)),
    gen_server:cast(Server, nonsense),

    ?assertEqual({ok, 55}, orchestrator_job_server:run({fib, 10})),
    ?assertEqual(Server, whereis(orchestrator_job_server)),
    ?assert(lists:member(Worker, worker_pids())).

detached_results_expire(_Config) ->
    Server = whereis(orchestrator_job_server),
    {ok, Id} = orchestrator_job_server:submit_detached(Server, {fib, 10}),
    wait_for_result(Id, {done, {ok, 55}}),
    wait_for_result(Id, {error, not_found}).

unknown_job_ids_are_not_found(_Config) ->
    Unknown = <<"ffffffffffffffff.", (atom_to_binary(node()))/binary>>,
    ?assertEqual({error, not_found}, orchestrator_job_server:result(Unknown)),
    ?assertEqual({error, not_found}, orchestrator_job_server:result(<<"nonsense">>)).

run_reports_a_job_server_restart(_Config) ->
    spawn_link(fun() ->
        wait_for_stats(#{busy => 1}),
        exit(whereis(orchestrator_job_server), kill)
    end),
    ?assertEqual({error, unavailable}, orchestrator_job_server:run(?LONG)).

%%% TCP front end

round_trip(_Config) ->
    Socket = connect(),
    ?assertEqual(<<"ok 55\n">>, request(Socket, <<"fib 10\n">>)),
    ?assertEqual(<<"ok\n">>, request(Socket, <<"sleep 1\n">>)),
    ?assertEqual(<<"ok queued=0 idle=2 busy=0\n">>, request(Socket, <<"status\n">>)),
    ?assertEqual(<<"error crashed\n">>, request(Socket, <<"crash\n">>)),
    ok = gen_tcp:close(Socket).

rejects_bad_requests(_Config) ->
    Socket = connect(),
    ?assertEqual(<<"error bad_request\n">>, request(Socket, <<"launch missiles\n">>)),
    ?assertEqual(<<"error bad_request\n">>, request(Socket, <<"fib 99999999\n">>)),
    %% The connection survives bad requests.
    ?assertEqual(<<"ok 1\n">>, request(Socket, <<"fib 1\n">>)),
    ok = gen_tcp:close(Socket).

answers_pipelined_requests_in_order(_Config) ->
    Socket = connect(),
    ok = gen_tcp:send(Socket, <<"sleep 50\nfib 10\nnope\nstatus\n">>),
    Replies = [recv(Socket) || _ <- lists:seq(1, 4)],
    ?assertEqual(
        [<<"ok\n">>, <<"ok 55\n">>, <<"error bad_request\n">>, <<"ok queued=0 idle=2 busy=0\n">>],
        Replies
    ),
    ok = gen_tcp:close(Socket).

serves_many_clients(_Config) ->
    %% More clients than acceptors, workers and queue slots combined. Each
    %% retries on `busy` until it's served.
    Clients = 20,
    Parent = self(),
    Pids = [
        spawn_link(fun() ->
            Socket = connect(),
            Parent ! {self(), request_until_accepted(Socket, <<"fib 20\n">>)}
        end)
     || _ <- lists:seq(1, Clients)
    ],
    Replies = [
        receive
            {Pid, Reply} -> Reply
        after 5000 -> ct:fail(timeout)
        end
     || Pid <- Pids
    ],
    ?assertEqual(lists:duplicate(Clients, <<"ok 6765\n">>), Replies).

half_close_still_gets_replies(_Config) ->
    Socket = connect(),
    ok = gen_tcp:send(Socket, <<"sleep 50\nfib 10\n">>),
    ok = gen_tcp:shutdown(Socket, write),
    ?assertEqual([<<"ok\n">>, <<"ok 55\n">>], [recv(Socket), recv(Socket)]),
    ?assertEqual({error, closed}, gen_tcp:recv(Socket, 0, 1000)).

reset_cancels_the_running_job(_Config) ->
    Socket = connect([{linger, {true, 0}}]),
    ok = gen_tcp:send(Socket, <<"sleep 10000\n">>),
    wait_for_stats(#{busy => 1}),

    %% Closing with a zero linger sends RST instead of FIN.
    ok = gen_tcp:close(Socket),
    wait_until_idle().

client_is_told_of_a_job_server_restart(_Config) ->
    Socket = connect(),
    ok = gen_tcp:send(Socket, <<"sleep 10000\n">>),
    wait_for_stats(#{busy => 1}),

    exit(whereis(orchestrator_job_server), kill),
    ?assertEqual(<<"error unavailable\n">>, recv(Socket)),
    %% The connection carries on with the restarted server.
    wait_until_idle(),
    ?assertEqual(<<"ok 1\n">>, request(Socket, <<"fib 1\n">>)).

idle_connections_are_closed(_Config) ->
    Socket = connect(),
    ?assertEqual(<<"ok 1\n">>, request(Socket, <<"fib 1\n">>)),
    ?assertEqual({error, closed}, gen_tcp:recv(Socket, 0, 2000)).

detached_job_survives_a_disconnect(_Config) ->
    Id = orchestrator_test_client:job_id(request_and_reset(<<"submit sleep 300\n">>)),

    Again = connect(),
    ?assertEqual(<<"pending\n">>, request(Again, ["result ", Id, "\n"])),
    wait_for_result(Id, {done, {ok, ok}}),
    ?assertEqual(<<"ok\n">>, request(Again, ["result ", Id, "\n"])),
    ?assertEqual(<<"error not_found\n">>, request(Again, <<"result nope\n">>)).

acceptors_answer_system_messages(_Config) ->
    Acceptors = [Pid || {_, Pid, _, _} <- supervisor:which_children(orchestrator_conn_sup)],
    ?assertNotEqual([], Acceptors),
    %% An acceptor stuck in a blocking accept would time out here.
    [{status, Pid, _, _} = sys:get_status(Pid, 3000) || Pid <- Acceptors].

%%% Helpers

submit(Task) ->
    {ok, Ref} = orchestrator_job_server:submit(Task),
    Ref.

await(Ref) ->
    receive
        {job_done, Ref, Result} -> Result
    after 5000 -> ct:fail({timeout, Ref})
    end.

%% Sends one request, then resets the connection, which would cancel a
%% normal job.
request_and_reset(Line) ->
    Socket = connect([{linger, {true, 0}}]),
    Reply = request(Socket, Line),
    ok = gen_tcp:close(Socket),
    Reply.

wait_for_result(Id, Expected) ->
    Done = fun() -> orchestrator_job_server:result(Id) =:= Expected end,
    orchestrator_test_client:eventually(Done).

worker_pids() ->
    lists:sort([Pid || {_, Pid, worker, _} <- supervisor:which_children(orchestrator_worker_sup)]).

wait_until_idle() ->
    wait_for_stats(#{idle => ?WORKERS, busy => 0, queued => 0}).

%% Waits until every key in `Expected` has that value in the job server's stats.
wait_for_stats(Expected) ->
    orchestrator_test_client:eventually(fun() ->
        maps:with(maps:keys(Expected), stats()) =:= Expected
    end).

%% The server may be mid-restart, in some tests.
stats() ->
    try
        orchestrator_job_server:stats()
    catch
        exit:{noproc, _} -> #{}
    end.

connect() ->
    connect([]).

request(Socket, Line) ->
    orchestrator_test_client:request(Socket, Line).

recv(Socket) ->
    orchestrator_test_client:recv(Socket).

connect(Extra) ->
    orchestrator_test_client:connect(orchestrator_listener:port(), Extra).

request_until_accepted(Socket, Line) ->
    case request(Socket, Line) of
        <<"error busy\n">> ->
            timer:sleep(10),
            request_until_accepted(Socket, Line);
        Reply ->
            Reply
    end.
