"""Bounded retention of verified project generations across authenticated queries."""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from dataclasses import fields, is_dataclass, replace
from typing import Any

from axiom_mcp.read_session import LoadedSnapshot


def _deny(*args: Any, **kwargs: Any) -> None:
    raise TypeError("retained graph data is read-only")


class _FrozenDict(dict):
    """JSON-compatible mapping with ordinary mutation operations disabled."""

    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _deny
    __ior__ = _deny


class _FrozenList(list):
    """JSON-compatible sequence; query builders cannot mutate a shared shard."""

    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = append = clear = extend = insert = pop = _deny
    remove = reverse = sort = __iadd__ = __imul__ = _deny


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        result = dict.__new__(_FrozenDict)
        for key, item in value.items():
            dict.__setitem__(result, key, _freeze(item))
        return result
    if isinstance(value, list):
        result = list.__new__(_FrozenList)
        list.extend(result, (_freeze(item) for item in value))
        return result
    return value


def retained_size(value: Any, seen: set[int] | None = None) -> int:
    """Count parsed objects, including containers; do not budget only JSON bytes."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        size += sum(retained_size(getattr(value, f.name), seen) for f in fields(value))
    elif isinstance(value, dict):
        size += sum(retained_size(k, seen) + retained_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(retained_size(v, seen) for v in value)
    return size


class GenerationRetention:
    """Thread-safe LRU of shared read-only inputs; query result state remains separate."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024, max_entries: int = 32):
        if type(max_bytes) is not int or type(max_entries) is not int:
            raise ValueError("cache budgets must be integers")
        if min(max_bytes, max_entries) < 1:
            raise ValueError("cache budgets must be positive")
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._entries: OrderedDict[tuple, tuple[LoadedSnapshot, int]] = OrderedDict()
        self._lock = threading.RLock()
        self._bytes = self._hits = self._misses = self._evictions = 0

    def get(self, key: tuple) -> LoadedSnapshot | None:
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value[0]

    def put(self, key: tuple, value: LoadedSnapshot) -> None:
        size = retained_size(value) + retained_size(key)
        if size > self.max_bytes:
            return
        owned = replace(
            value, shards=tuple(replace(s, document=_freeze(s.document)) for s in value.shards)
        )
        size = retained_size(owned) + retained_size(key)
        if size > self.max_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous:
                self._bytes -= previous[1]
            self._entries[key] = (owned, size)
            self._bytes += size
            while self._bytes > self.max_bytes or len(self._entries) > self.max_entries:
                _, (_, old_size) = self._entries.popitem(last=False)
                self._bytes -= old_size
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(
                entries=len(self._entries),
                bytes=self._bytes,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
            )
