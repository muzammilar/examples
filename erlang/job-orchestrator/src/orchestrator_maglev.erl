-module(orchestrator_maglev).
-moduledoc """
Maglev consistent hashing: maps keys to nodes so that every node owns an
almost equal share, and a node joining or leaving moves few other keys.

Each node gets its own pseudo-random order of the table's slots, derived
from its name. The nodes take turns claiming their next free slot until
the table is full; a key then belongs to whichever node owns the slot it
hashes to. See Eisenbud et al., "Maglev: A Fast and Reliable Software
Network Load Balancer" (NSDI 2016).

Everything is derived from the node names with `erlang:phash2/2`, which is
stable across nodes and OTP releases. Every node that knows the same members
builds the same table without talking to the others.
""".

-export([new/1, lookup/2]).
-export_type([table/0]).

%% Prime, as the algorithm requires, and far larger than the member count,
%% which keeps shares close to equal.
-define(SIZE, 1021).

-opaque table() :: {Members :: tuple(), Slots :: tuple()}.

-doc "Builds a table for `Members`, which must be unique. Their order doesn't matter.".
-spec new([term(), ...]) -> table().
new([_ | _] = Members0) when length(Members0) < ?SIZE ->
    Members = list_to_tuple(lists:usort(Members0)),
    Count = tuple_size(Members),
    Prefs = [preference(element(I, Members), ?SIZE) || I <- lists:seq(1, Count)],
    Slots = fill(Prefs, ?SIZE),
    {Members, list_to_tuple([element(Owner, Members) || Owner <- slot_owners(Slots, ?SIZE)])}.

-doc "The member that owns `Key`.".
-spec lookup(term(), table()) -> term().
lookup(Key, {_Members, Slots}) ->
    element(erlang:phash2(Key, tuple_size(Slots)) + 1, Slots).

%% A member's slot order is `Offset, Offset + Skip, Offset + 2 * Skip, ...`,
%% modulo the table size. Skip is never zero and the size is prime, so this
%% visits every slot exactly once.
preference(Member, Size) ->
    Offset = erlang:phash2({offset, Member}, Size),
    Skip = erlang:phash2({skip, Member}, Size - 1) + 1,
    {Offset, Skip, 0}.

%% Members take turns, in order, each claiming the next slot in its own
%% order that nobody has claimed yet, until every slot is taken.
fill(Prefs, Size) ->
    fill(list_to_tuple(Prefs), 1, #{}, Size).

fill(_Prefs, _Member, Claimed, Size) when map_size(Claimed) =:= Size ->
    Claimed;
fill(Prefs, Member, Claimed, Size) ->
    {Offset, Skip, Tried} = element(Member, Prefs),
    {Slot, Tried1} = next_free(Offset, Skip, Tried, Claimed, Size),
    Prefs1 = setelement(Member, Prefs, {Offset, Skip, Tried1 + 1}),
    fill(Prefs1, Member rem tuple_size(Prefs) + 1, Claimed#{Slot => Member}, Size).

next_free(Offset, Skip, Tried, Claimed, Size) ->
    Slot = (Offset + Tried * Skip) rem Size,
    case is_map_key(Slot, Claimed) of
        true -> next_free(Offset, Skip, Tried + 1, Claimed, Size);
        false -> {Slot, Tried}
    end.

slot_owners(Claimed, Size) ->
    [maps:get(Slot, Claimed) || Slot <- lists:seq(0, Size - 1)].
