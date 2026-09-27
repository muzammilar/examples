-module(orchestrator_worker).
-moduledoc """
Runs one job at a time for the job server.

Each task runs in its own process. A task that crashes reports its own
crash; one that hangs is killed once `job_timeout` passes. Either way the
worker carries on with the next job.
""".
-behaviour(gen_server).

-export([start_link/0, run/3, cancel/2]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2]).

-record(running, {
    ref :: orchestrator_job_server:job_ref(),
    task :: orchestrator_task:task(),
    pid :: pid(),
    monitor :: reference(),
    timer :: reference()
}).

-record(state, {
    job_timeout :: pos_integer(),
    running :: #running{} | idle
}).

-spec start_link() -> gen_server:start_ret().
start_link() ->
    gen_server:start_link(?MODULE, [], []).

-doc "Asks `Worker` to run `Task`, reporting back under `Ref`.".
-spec run(pid(), orchestrator_job_server:job_ref(), orchestrator_task:task()) -> ok.
run(Worker, Ref, Task) ->
    gen_server:cast(Worker, {run, Ref, Task}).

-doc "Asks `Worker` to abandon job `Ref`, if it's still running it.".
-spec cancel(pid(), orchestrator_job_server:job_ref()) -> ok.
cancel(Worker, Ref) ->
    gen_server:cast(Worker, {cancel, Ref}).

-doc false.
init([]) ->
    process_flag(trap_exit, true),
    {ok, Timeout} = application:get_env(orchestrator, job_timeout),
    orchestrator_job_server:checkin(self()),
    {ok, #state{job_timeout = Timeout, running = idle}}.

-doc false.
handle_call(Request, _From, State) ->
    {stop, {unexpected_call, Request}, State}.

-doc false.
handle_cast({run, Ref, Task}, #state{running = idle} = State) ->
    Worker = self(),
    Run = fun() -> Worker ! {task_result, Ref, run_task(Task)} end,
    {Pid, Mon} = spawn_opt(Run, [link, monitor]),
    Timer = erlang:start_timer(State#state.job_timeout, self(), Ref),
    Running = #running{ref = Ref, task = Task, pid = Pid, monitor = Mon, timer = Timer},
    {noreply, State#state{running = Running}};
handle_cast({cancel, Ref}, #state{running = #running{ref = Ref}} = State) ->
    {noreply, finish(cancelled, State)};
handle_cast({cancel, _Stale}, State) ->
    {noreply, State}.

-doc false.
handle_info({task_result, Ref, Outcome}, #state{running = #running{ref = Ref}} = State) ->
    {noreply, finish(Outcome, State)};
handle_info({task_result, _StaleRef, _Outcome}, State) ->
    %% Sent just before its job was cancelled or timed out.
    {noreply, State};
handle_info({'DOWN', Mon, process, _, Reason}, #state{running = #running{monitor = Mon}} = State) ->
    %% A task always reports before exiting, and signals between two
    %% processes arrive in order: this means it was killed from outside.
    {noreply, finish({crashed, Reason}, State)};
handle_info({timeout, Timer, _Ref}, #state{running = #running{timer = Timer} = Running} = State) ->
    logger:notice("job ~p timed out after ~b ms", [Running#running.task, State#state.job_timeout]),
    {noreply, finish(timeout, State)};
handle_info({timeout, _StaleTimer, _Ref}, State) ->
    %% Fired just as its job finished some other way.
    {noreply, State};
handle_info({'EXIT', _Task, _Reason}, State) ->
    %% The link is only there to take the task down with us; the monitor
    %% already reported how it ended.
    {noreply, State}.

%% Catching the error here keeps a crashing job from filling the log with
%% crash reports.
run_task(Task) ->
    try
        {ok, orchestrator_task:run(Task)}
    catch
        Class:Reason:Stacktrace -> {crashed, {Class, Reason, Stacktrace}}
    end.

finish(
    Outcome, #state{running = #running{ref = Ref, pid = Pid, monitor = Mon, timer = Timer}} = State
) ->
    demonitor(Mon, [flush]),
    exit(Pid, kill),
    _ = erlang:cancel_timer(Timer),
    orchestrator_job_server:complete(self(), Ref, Outcome),
    State#state{running = idle}.
