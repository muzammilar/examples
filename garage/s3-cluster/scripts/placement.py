"""Read-only lookup of Garage 2.5/2.6's persisted, natively computed replica ring.

This reads G010lh MessagePack, then uses the same partition/table lookup as
LayoutVersion::{partition_of,nodes_of}. No placement assignment is recomputed.
Reject unknown formats, transitional layouts and changed fixture membership.
"""

import hashlib
import json
from pathlib import Path

import msgpack

MARKER = b"G010lh"


def decode_layout(data, peers):
    if not data.startswith(MARKER):
        raise RuntimeError("unknown native layout marker")
    layout = msgpack.unpackb(data[len(MARKER) :], raw=False)
    versions = layout["versions"]
    if len(versions) != 1:
        raise RuntimeError("transitional native layouts are not qualified")
    current = versions[0]
    nodes = current["node_id_vec"]
    if any(not isinstance(n, bytes) or len(n) != 32 for n in nodes):
        raise RuntimeError("unknown native node ID representation")
    mapping = {node.split("@")[0]: i for i, node in enumerate(peers)}
    if len(mapping) != 9 or {n.hex() for n in nodes} != set(mapping):
        raise RuntimeError("native ring membership differs from the fixture")
    ring = current["ring_assignment_data"]
    if (
        current["replication_factor"] != 3
        or current["nongateway_node_count"] != 9
        or not isinstance(ring, bytes)
        or len(ring) != 768
    ):
        raise RuntimeError("expected native 256-partition, three-copy ring")
    owners = []
    for partition in range(256):
        indices = ring[3 * partition : 3 * (partition + 1)]
        if any(i >= len(nodes) for i in indices):
            raise RuntimeError("native ring index out of range")
        assigned = sorted(mapping[nodes[i].hex()] for i in indices)
        if len(set(assigned)) != 3 or {i // 3 for i in assigned} != {0, 1, 2}:
            raise RuntimeError("native ring violates configured three-zone placement")
        owners.append(assigned)
    return {"version": current["version"], "owners": owners}


def load_ring(peers, directory=Path("/metadata")):
    values = [
        decode_layout((directory / str(i) / "cluster_layout").read_bytes(), peers)
        for i in range(9)
    ]
    if any(v != values[0] for v in values):
        raise RuntimeError("native replica assignments differ across metadata copies")
    value = values[0]
    value["sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True).encode()
    ).hexdigest()
    return value


def owners_of(block_hash, ring):
    # PARTITION_BITS=8; native partition_of takes the top eight hash bits.
    raw = bytes.fromhex(block_hash)
    if len(raw) != 32:
        raise ValueError("expected a complete 32-byte block hash")
    return set(ring["owners"][raw[0]])


def placement_errors(expected, copies, ring):
    missing, misplaced = [], []
    for block in expected:
        actual = set(copies.get(block, []))
        wanted = owners_of(block, ring)
        if wanted - actual:
            missing.append(block)
        if actual - wanted:
            misplaced.append(block)
    return missing, misplaced
