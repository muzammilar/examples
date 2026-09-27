-module(orchestrator_conn).
-moduledoc """
One process per client connection. It starts as an acceptor, then serves the
client's requests one at a time, in order, buffering up to `MAX_PIPELINED`
lines. After the client half-closes, buffered requests are still answered.
A connection idle for `idle_timeout` is closed.

Limitation: a half-close looks the same whether the client is done sending
or gone. A client that disconnects mid-job still has that job run, up to
`job_timeout`.
""".
-behaviour(gen_server).

-export([start_link/1]).
-export([init/1, handle_call/3, handle_cast/2, handle_info/2, handle_continue/2]).

-define(MAX_PIPELINED, 16).
%% Accepting waits at most this long at a time, then goes back through the
%% mailbox to answer system messages.
-define(ACCEPT_POLL_MS, 1000).
-define(ACCEPT_BACKOFF_MS, 100).

-record(state, {
    listen :: gen_tcp:socket(),
    socket :: gen_tcp:socket() | undefined,
    %% The job in flight, and a monitor on the job server running it.
    running = idle :: {orchestrator_job_server:job_ref(), reference()} | idle,
    pending = queue:new() :: queue:queue(binary()),
    %% The client has closed its side; finish up, then close ours.
    closing = false :: boolean(),
    %% A reply couldn't be sent: the client is gone.
    gone = false :: boolean(),
    idle_timer :: reference() | undefined,
    %% Set while accepts keep failing; the failure is logged once.
    accept_failing = false :: boolean()
}).

-spec start_link(gen_tcp:socket()) -> gen_server:start_ret().
start_link(ListenSocket) ->
    gen_server:start_link(?MODULE, ListenSocket, []).

-doc false.
init(ListenSocket) ->
    {ok, #state{listen = ListenSocket}, {continue, accept}}.

-doc false.
handle_continue(accept, #state{listen = Listen} = State) ->
    case gen_tcp:accept(Listen, ?ACCEPT_POLL_MS) of
        {ok, Socket} ->
            {ok, _} = orchestrator_conn_sup:start_acceptor(Listen),
            continue(State#state{socket = Socket, accept_failing = false});
        {error, timeout} ->
            self() ! accept,
            {noreply, State};
        {error, closed} ->
            %% The listener went away; it will start fresh acceptors.
            {stop, normal, State};
        {error, Reason} ->
            %% Typically emfile: out of file descriptors. Keep this
            %% acceptor alive and try again once some may have freed up.
            case State#state.accept_failing of
                true -> ok;
                false -> logger:warning("accept failing: ~p", [Reason])
            end,
            erlang:send_after(?ACCEPT_BACKOFF_MS, self(), accept),
            {noreply, State#state{accept_failing = true}}
    end.

-doc false.
handle_info(accept, #state{socket = undefined} = State) ->
    {noreply, State, {continue, accept}};
handle_info({tcp, Socket, Line}, #state{socket = Socket, pending = Pending} = State) ->
    continue(State#state{pending = queue:in(Line, Pending)});
handle_info({job_done, Ref, Result}, #state{running = {Ref, Mon}} = State) ->
    demonitor(Mon, [flush]),
    continue(send(Result, State#state{running = idle}));
handle_info({job_done, _StaleRef, _Result}, State) ->
    %% Already answered as unavailable when its server went down.
    {noreply, State};
handle_info({'DOWN', Mon, process, _, _}, #state{running = {_, Mon}} = State) ->
    %% The job server restarted, losing the job; the result will never come.
    continue(send({error, unavailable}, State#state{running = idle}));
handle_info({tcp_closed, Socket}, #state{socket = Socket} = State) ->
    continue(State#state{closing = true});
handle_info({tcp_error, Socket, _Reason}, #state{socket = Socket} = State) ->
    %% Usually a reset: the client is gone, which is no fault of ours.
    {stop, normal, State};
handle_info({timeout, Timer, idle}, #state{idle_timer = Timer} = State) ->
    {stop, normal, State};
handle_info({timeout, _StaleTimer, idle}, State) ->
    {noreply, State};
handle_info(Msg, State) ->
    logger:warning("unexpected message: ~p", [Msg]),
    {noreply, State}.

-doc false.
handle_call(Request, _From, State) ->
    {stop, {unexpected_call, Request}, State}.

-doc false.
handle_cast(Msg, State) ->
    {stop, {unexpected_cast, Msg}, State}.

%% Every event ends here: start whatever can start, then decide whether the
%% connection is finished, and if not, whether to read more.
continue(State0) ->
    State = next(State0),
    Idle = State#state.running =:= idle andalso queue:is_empty(State#state.pending),
    case State of
        #state{gone = true} -> {stop, normal, State};
        #state{closing = true} when Idle -> {stop, normal, State};
        _ -> {noreply, reset_idle_timer(Idle, arm(State))}
    end.

next(#state{gone = true} = State) ->
    State;
next(#state{running = idle, pending = Pending} = State) ->
    case queue:out(Pending) of
        {{value, Line}, Rest} -> next(start(Line, State#state{pending = Rest}));
        {empty, _} -> State
    end;
next(State) ->
    State.

start(Line, State) ->
    case orchestrator_protocol:decode(Line) of
        {ok, {run, Task, Key}} ->
            case submit(Task, Key) of
                {ok, Job} -> State#state{running = Job};
                {error, _} = Error -> send(Error, State)
            end;
        {ok, {submit, Task, Key}} ->
            send(submit_detached(Task, Key), State);
        {ok, {result, Id}} ->
            send(result(Id), State);
        {ok, status} ->
            send(stats(), State);
        {ok, nodes} ->
            send({nodes, orchestrator_cluster:members()}, State);
        {ok, {owner, Key}} ->
            case orchestrator_cluster:owner(Key) of
                {ok, Server} -> send({owner, node(Server)}, State);
                {error, _} = Error -> send(Error, State)
            end;
        {error, _} = Error ->
            send(Error, State)
    end.

%% The server is resolved once: the monitor and the call target the same
%% process even if it is restarting. A server that's down or mid-restart
%% gets the client an `unavailable` reply.
submit(Task, Key) ->
    case job_server(Key) of
        {ok, Server} -> submit_to(Server, Task);
        {error, _} = Error -> Error
    end.

submit_to(Server, Task) ->
    Mon = monitor(process, Server),
    try orchestrator_job_server:submit(Server, Task) of
        {ok, Ref} ->
            {ok, {Ref, Mon}};
        {error, busy} = Busy ->
            demonitor(Mon, [flush]),
            Busy
    catch
        exit:_ ->
            demonitor(Mon, [flush]),
            {error, unavailable}
    end.

%% No monitor: a detached job outlives the connection.
submit_detached(Task, Key) ->
    maybe
        {ok, Server} ?= job_server(Key),
        {ok, Id} ?=
            try
                orchestrator_job_server:submit_detached(Server, Task)
            catch
                exit:_ -> {error, unavailable}
            end,
        {job, Id}
    end.

result(Id) ->
    case orchestrator_job_server:result(Id) of
        {done, Result} -> Result;
        Other -> Other
    end.

%% A keyed job's server is on the key's owner node; an unkeyed job's is here.
job_server(undefined) ->
    case whereis(orchestrator_job_server) of
        undefined -> {error, unavailable};
        Server -> {ok, Server}
    end;
job_server(Key) ->
    orchestrator_cluster:owner(Key).

stats() ->
    try
        {status, orchestrator_job_server:stats()}
    catch
        exit:_ -> {error, unavailable}
    end.

send(_Reply, #state{gone = true} = State) ->
    State;
send(Reply, #state{socket = Socket} = State) ->
    case gen_tcp:send(Socket, orchestrator_protocol:encode(Reply)) of
        ok -> State;
        {error, _} -> State#state{gone = true}
    end.

arm(#state{closing = true} = State) ->
    State;
arm(#state{socket = Socket, pending = Pending} = State) ->
    _ = queue:len(Pending) < ?MAX_PIPELINED andalso inet:setopts(Socket, [{active, once}]),
    State.

%% The timer runs only while there's nothing to do; a slow job doesn't count
%% as the client being idle.
reset_idle_timer(Idle, #state{idle_timer = Old} = State) ->
    _ = Old =/= undefined andalso erlang:cancel_timer(Old),
    {ok, IdleTimeout} = application:get_env(orchestrator, idle_timeout),
    Timer =
        case Idle of
            true -> erlang:start_timer(IdleTimeout, self(), idle);
            false -> undefined
        end,
    State#state{idle_timer = Timer}.
