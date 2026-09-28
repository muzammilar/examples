-module(orchestrator_job_server).
-moduledoc """
Queues jobs and hands them to idle workers.

`submit/2` returns a job reference; the result arrives later as a
`{job_done, JobRef, Result}` message. `submit/1`, `cancel/1`, `run/1` and
`run/2` work on the local server and are the API for tests and the shell;
`run` blocks until the result arrives.

A detached job (`submit_detached/2`) is tied to no caller. It runs to the
end, and its result is kept for `result_ttl`: a client can disconnect and
collect it later from any node with `result/1`.

The server monitors every other caller. If one dies, its queued jobs are
dropped and its running jobs cancelled. It also monitors every worker: a
worker that dies mid-job has that job retried, like a job whose task crashed.

Limitation: jobs live only in this process's memory. If it restarts or its
node goes down, every job queued or running here is lost, kept results
included, and waiting callers get `{error, unavailable}`.
""".
-behaviour(gen_server).

-export([start_link/0, submit/1, submit/2, cancel/1, run/1, run/2, stats/0]).
-export([submit_detached/2, result/1, node_of/1]).
-export([checkin/1, complete/3]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2]).

-export_type([job_ref/0, job_id/0, result/0, outcome/0, stats/0]).

-type job_ref() :: reference().
%% A detached job's name for clients: `<random hex>.<node>`. The node part
%% says where to ask for its result.
-type job_id() :: binary().
-type result() ::
    {ok, orchestrator_task:result()}
    | {error, {crashed, Reason :: term()} | timeout | unavailable}.
-type outcome() :: {ok, orchestrator_task:result()} | {crashed, term()} | timeout | cancelled.
-type stats() :: #{
    queued := non_neg_integer(), idle := non_neg_integer(), busy := non_neg_integer()
}.

-record(job, {
    ref :: job_ref(),
    task :: orchestrator_task:task(),
    %% Who gets the result: a monitored process, or nobody yet, for a
    %% detached job whose result is kept until someone asks. `cancelled`
    %% once cancelled while running: its outcome is dropped, not delivered
    %% or retried.
    caller :: {monitored, pid(), reference()} | {detached, job_id()} | cancelled,
    attempts = 0 :: non_neg_integer()
}).

-record(state, {
    queue = queue:new() :: queue:queue(#job{}),
    idle = [] :: [pid()],
    busy = #{} :: #{pid() => #job{}},
    %% Caller monitor => the job it's waiting on.
    callers = #{} :: #{reference() => job_ref()},
    %% Detached jobs not yet finished, and finished ones' results until they
    %% expire.
    %%
    %% Limitation: nothing caps how many results are kept; a burst of
    %% detached jobs holds all their results in memory for `result_ttl`.
    detached = #{} :: #{job_id() => pending | result()},
    result_ttl :: pos_integer(),
    max_queue :: non_neg_integer(),
    max_attempts :: pos_integer()
}).

%%% Client API

-spec start_link() -> gen_server:start_ret().
start_link() ->
    gen_server:start_link({local, ?MODULE}, ?MODULE, [], []).

-doc """
Queues `Task`. The caller later receives `{job_done, JobRef, Result}`.
Returns `{error, busy}` when the queue is full.
""".
-spec submit(orchestrator_task:task()) -> {ok, job_ref()} | {error, busy}.
submit(Task) ->
    submit(?MODULE, Task).

-doc "Like `submit/1`, on a server found by the caller.".
-spec submit(gen_server:server_ref(), orchestrator_task:task()) -> {ok, job_ref()} | {error, busy}.
submit(Server, Task) ->
    gen_server:call(Server, {submit, Task}).

-doc "Queues `Task` on `Server` as a detached job and returns its ID.".
-spec submit_detached(gen_server:server_ref(), orchestrator_task:task()) ->
    {ok, job_id()} | {error, busy}.
submit_detached(Server, Task) ->
    gen_server:call(Server, {submit_detached, Task}).

-doc """
The result of a detached job, asked of the node that ran it, wherever the
caller is.
""".
-spec result(job_id()) -> {done, result()} | pending | {error, not_found | unavailable}.
result(Id) ->
    maybe
        {ok, Node} ?= node_of(Id),
        try gen_server:call({?MODULE, Node}, {result, Id}) of
            not_found -> {error, not_found};
            Reply -> Reply
        catch
            exit:_ -> {error, unavailable}
        end
    end.

-doc """
The node a job ID says ran the job. Only nodes this one has heard of count,
so a made-up ID can't create atoms.
""".
-spec node_of(binary()) -> {ok, node()} | {error, not_found}.
node_of(Id) ->
    case binary:split(Id, <<".">>) of
        [_Hex, Node] ->
            try
                {ok, binary_to_existing_atom(Node)}
            catch
                error:badarg -> {error, not_found}
            end;
        _ ->
            {error, not_found}
    end.

-doc "Cancels a job. No `job_done` message is sent for it afterwards.".
-spec cancel(job_ref()) -> ok.
cancel(Ref) ->
    gen_server:call(?MODULE, {cancel, Ref}).

-doc #{equiv => run(Task, infinity)}.
-spec run(orchestrator_task:task()) -> result() | {error, busy}.
run(Task) ->
    run(Task, infinity).

-doc """
Submits `Task` and waits up to `Timeout` for its result. On timeout the job
is cancelled and `{error, timeout}` returned.
""".
-spec run(orchestrator_task:task(), timeout()) -> result() | {error, busy}.
run(Task, Timeout) ->
    case whereis(?MODULE) of
        undefined -> {error, unavailable};
        Server -> run(Server, Task, Timeout)
    end.

run(Server, Task, Timeout) ->
    Mon = monitor(process, Server),
    try
        maybe
            {ok, Ref} ?= submit(Server, Task),
            receive
                {job_done, Ref, Result} -> Result;
                {'DOWN', Mon, process, _, _} -> {error, unavailable}
            after Timeout ->
                ok = cancel(Ref),
                %% The result may have landed just before the cancel did.
                receive
                    {job_done, Ref, _} -> ok
                after 0 -> ok
                end,
                {error, timeout}
            end
        end
    catch
        %% The server went down between being found and answering.
        exit:_ -> {error, unavailable}
    after
        demonitor(Mon, [flush])
    end.

-doc "Snapshot of queue depth and worker utilisation.".
-spec stats() -> stats().
stats() ->
    gen_server:call(?MODULE, stats).

-doc "Called by a worker when it starts, to join the pool.".
-spec checkin(pid()) -> ok.
checkin(Worker) ->
    gen_server:cast(?MODULE, {checkin, Worker}).

-doc "Called by a worker when its job ends, however it ended.".
-spec complete(pid(), job_ref(), outcome()) -> ok.
complete(Worker, Ref, Outcome) ->
    gen_server:cast(?MODULE, {complete, Worker, Ref, Outcome}).

%%% gen_server callbacks

-doc false.
init([]) ->
    %% So other nodes can route keyed jobs here; see orchestrator_cluster.
    ok = pg:join(job_servers, self()),
    {ok, MaxQueue} = application:get_env(orchestrator, max_queue),
    {ok, MaxAttempts} = application:get_env(orchestrator, max_attempts),
    {ok, ResultTtl} = application:get_env(orchestrator, result_ttl),
    {ok, #state{max_queue = MaxQueue, max_attempts = MaxAttempts, result_ttl = ResultTtl}}.

-doc false.
handle_call({Submit, Task}, From, #state{idle = []} = State) when
    Submit =:= submit; Submit =:= submit_detached
->
    %% With no idle worker, a job needs a queue slot.
    case queue:len(State#state.queue) >= State#state.max_queue of
        true -> {reply, {error, busy}, State};
        false -> accept(Submit, Task, From, State)
    end;
handle_call({Submit, Task}, From, State) when Submit =:= submit; Submit =:= submit_detached ->
    accept(Submit, Task, From, State);
handle_call({result, Id}, _From, State) ->
    Reply =
        case State#state.detached of
            #{Id := pending} -> pending;
            #{Id := Result} -> {done, Result};
            #{} -> not_found
        end,
    {reply, Reply, State};
handle_call({cancel, Ref}, _From, State) ->
    {reply, ok, cancel_job(Ref, State)};
handle_call(stats, _From, State) ->
    #state{queue = Queue, idle = Idle, busy = Busy} = State,
    {reply, #{queued => queue:len(Queue), idle => length(Idle), busy => map_size(Busy)}, State};
handle_call(Request, From, State) ->
    logger:warning("unexpected call from ~p: ~p", [From, Request]),
    {reply, {error, unknown_request}, State}.

-doc false.
handle_cast({checkin, Worker}, State) ->
    _ = monitor(process, Worker),
    {noreply, dispatch(State#state{idle = [Worker | State#state.idle]})};
handle_cast({complete, Worker, Ref, Outcome}, State) ->
    %% A worker runs one job at a time and reports only on the one it was
    %% given: this always matches.
    {#job{ref = Ref} = Job, Busy} = maps:take(Worker, State#state.busy),
    Freed = State#state{busy = Busy, idle = [Worker | State#state.idle]},
    {noreply, dispatch(settle(Job, Outcome, Freed))};
handle_cast(Msg, State) ->
    logger:warning("unexpected cast: ~p", [Msg]),
    {noreply, State}.

-doc false.
handle_info({'DOWN', Mon, process, Pid, Reason}, State) ->
    case maps:find(Mon, State#state.callers) of
        {ok, Ref} ->
            {noreply, cancel_job(Ref, State)};
        error ->
            {noreply, dispatch(worker_down(Pid, Reason, State))}
    end;
handle_info({expire, Id}, State) ->
    {noreply, State#state{detached = maps:remove(Id, State#state.detached)}};
handle_info(Msg, State) ->
    logger:warning("unexpected message: ~p", [Msg]),
    {noreply, State}.

%%% Internal

accept(submit, Task, {Caller, _Tag}, State) ->
    Ref = make_ref(),
    Mon = monitor(process, Caller),
    Job = #job{ref = Ref, task = Task, caller = {monitored, Caller, Mon}},
    Queue = queue:in(Job, State#state.queue),
    Callers = (State#state.callers)#{Mon => Ref},
    {reply, {ok, Ref}, dispatch(State#state{queue = Queue, callers = Callers})};
accept(submit_detached, Task, _From, State) ->
    Id = new_id(),
    Job = #job{ref = make_ref(), task = Task, caller = {detached, Id}},
    Queue = queue:in(Job, State#state.queue),
    Detached = (State#state.detached)#{Id => pending},
    {reply, {ok, Id}, dispatch(State#state{queue = Queue, detached = Detached})}.

new_id() ->
    Hex = binary:encode_hex(rand:bytes(8), lowercase),
    <<Hex/binary, ".", (atom_to_binary(node()))/binary>>.

dispatch(#state{idle = [Worker | Idle], queue = Queue, busy = Busy} = State) ->
    case queue:out(Queue) of
        {{value, #job{ref = Ref, task = Task} = Job}, Rest} ->
            orchestrator_worker:run(Worker, Ref, Task),
            dispatch(State#state{idle = Idle, queue = Rest, busy = Busy#{Worker => Job}});
        {empty, _} ->
            State
    end;
dispatch(#state{idle = []} = State) ->
    State.

settle(Job, {ok, Result}, State) -> deliver(Job, {ok, Result}, State);
settle(Job, timeout, State) -> deliver(Job, {error, timeout}, State);
settle(Job, {crashed, Reason}, State) -> retry_or_fail(Job, Reason, State);
settle(_Job, cancelled, State) -> State.

deliver(#job{caller = cancelled}, _Result, State) ->
    State;
deliver(#job{ref = Ref, caller = {monitored, Pid, Mon}}, Result, State) ->
    Pid ! {job_done, Ref, Result},
    forget_caller(Mon, State);
deliver(#job{caller = {detached, Id}}, Result, State) ->
    erlang:send_after(State#state.result_ttl, self(), {expire, Id}),
    State#state{detached = (State#state.detached)#{Id := Result}}.

retry_or_fail(#job{caller = cancelled}, _Reason, State) ->
    State;
retry_or_fail(#job{attempts = Attempts} = Job, Reason, State) ->
    case Attempts + 1 of
        Max when Max >= State#state.max_attempts ->
            logger:notice("job ~p failed after ~b attempts: ~p", [Job#job.task, Max, Reason]),
            deliver(Job, {error, {crashed, Reason}}, State);
        Next ->
            logger:notice("job ~p crashed (attempt ~b of ~b), retrying: ~p", [
                Job#job.task, Next, State#state.max_attempts, Reason
            ]),
            %% Front of the queue: it has already waited its turn once.
            Retry = Job#job{attempts = Next},
            State#state{queue = queue:in_r(Retry, State#state.queue)}
    end.

%% Limitation: a job sent to a worker that was already dying still counts as
%% an attempt, though its task never started.
worker_down(Worker, Reason, State) ->
    Idle = lists:delete(Worker, State#state.idle),
    case maps:take(Worker, State#state.busy) of
        {Job, Busy} ->
            retry_or_fail(Job, {worker_down, Reason}, State#state{idle = Idle, busy = Busy});
        error ->
            State#state{idle = Idle}
    end.

cancel_job(Ref, State) ->
    %% Only a monitored caller can cancel; detached jobs have none.
    IsJob = fun
        (#job{ref = R, caller = {monitored, _, _}}) -> R =:= Ref;
        (#job{}) -> false
    end,
    Running = [W || W := Job <- State#state.busy, IsJob(Job)],
    case {queue:any(IsJob, State#state.queue), Running} of
        {true, []} ->
            {value, #job{caller = {monitored, _, Mon}}} = lists:search(
                IsJob, queue:to_list(State#state.queue)
            ),
            Queue = queue:delete_with(IsJob, State#state.queue),
            forget_caller(Mon, State#state{queue = Queue});
        {false, [Worker]} ->
            #job{caller = {monitored, _, Mon}} = Job = maps:get(Worker, State#state.busy),
            orchestrator_worker:cancel(Worker, Ref),
            Busy = (State#state.busy)#{Worker := Job#job{caller = cancelled}},
            forget_caller(Mon, State#state{busy = Busy});
        {false, []} ->
            %% Already finished, or already cancelled.
            State
    end.

forget_caller(Mon, State) ->
    demonitor(Mon, [flush]),
    State#state{callers = maps:remove(Mon, State#state.callers)}.
