import pickle
import json as json_mod
from typing import Callable

_registry: dict[str, tuple[Callable, Callable]] = {}


def register(name: str, serialize_fn: Callable, deserialize_fn: Callable):
    _registry[name] = (serialize_fn, deserialize_fn)


def serialize(name: str, value) -> bytes:
    if name not in _registry:
        raise ValueError(f"Unknown serializer: {name!r}")
    return _registry[name][0](value)


def deserialize(name: str, data: bytes):
    if name not in _registry:
        raise ValueError(f"Unknown serializer: {name!r}")
    return _registry[name][1](data)


register("pickle", pickle.dumps, pickle.loads)
register(
    "json",
    lambda v: json_mod.dumps(v).encode(),
    lambda d: json_mod.loads(d.decode()),
)
