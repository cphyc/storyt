"""Fluent query API for traversing and materialising asset hierarchies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from dask import compute as dask_compute, delayed

from . import serializers
from .instance import AssetInstance, _search_asset

if TYPE_CHECKING:
    from collections.abc import Callable

    from .asset import StaticAsset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topo_sort_props(asset: StaticAsset, requested: tuple[str, ...]) -> list[str]:
    """Return property names in dependency order (dependencies first).

    Raises RuntimeError on circular dependencies.
    """
    order: list[str] = []
    visited: set[str] = set()
    in_stack: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in in_stack:
            raise RuntimeError(f"Circular property dependency involving '{name}'")
        prop = asset._properties.get(name)
        if prop is None:
            return
        in_stack.add(name)
        for dep in prop.requires:
            visit(dep)
        in_stack.discard(name)
        visited.add(name)
        order.append(name)

    for name in requested:
        visit(name)
    return order


def _make_instance(asset: StaticAsset, row: dict) -> AssetInstance:
    keys = json.loads(row["keys"]) if isinstance(row["keys"], str) else row["keys"]
    path = Path(row["path"]) if row["path"] else None
    return AssetInstance(asset, row["id"], path, keys, row.get("parent_id"))


def _compute_prop_cached(instance: AssetInstance, prop, dep_values: tuple = ()):
    """Compute one property (with cache check/store). Safe to call from threads."""
    source_hash = prop.source_hash()
    db = instance.asset._db
    prop_id = db.register_property(
        instance.asset._db_id, prop.name, source_hash, prop.serializer
    )

    cached = db.get_cached_property(prop_id, instance.db_id, source_hash)
    if cached is not None:
        return serializers.deserialize(prop.serializer, cached)

    value = prop.compute(instance, dep_values)
    db.set_cached_property(
        prop_id,
        instance.db_id,
        serializers.serialize(prop.serializer, value),
        source_hash,
    )
    return value


# ---------------------------------------------------------------------------
# Query class
# ---------------------------------------------------------------------------


class Query:
    """A lazy, chainable view over a set of AssetInstances."""

    def __init__(self, asset: StaticAsset, instances: list[AssetInstance]):
        self._asset = asset
        self._instances = instances

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def all(self) -> Query:
        """Return self (all instances are already included)."""
        return self

    def __getattr__(self, name: str) -> Query:
        """Chain to a related asset type by name."""
        if name.startswith("_"):
            raise AttributeError(name)

        # Find the target asset anywhere in the hierarchy
        root = self._asset
        while root._parent is not None:
            root = root._parent

        target = _search_asset(root, name)
        if target is None or target._db_id is None:
            raise AttributeError(f"No discovered asset named {name!r} in hierarchy")

        db = self._asset._db
        related: list[AssetInstance] = []
        seen_ids: set[int] = set()

        for inst in self._instances:
            # Direct filesystem children
            for row in db.get_child_instances(inst.db_id, target._db_id):
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    related.append(_make_instance(target, row))
            # Bound siblings/cousins
            for row in db.get_bound_instances(inst.db_id, target._db_id):
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    related.append(_make_instance(target, row))

        return Query(target, related)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def query(self, predicate: Callable[[AssetInstance], bool]) -> Query:
        """Filter instances by a predicate.

        The predicate receives an AssetInstance; property access on it is
        DB-backed so cached values are used where available.
        """
        return Query(self._asset, [i for i in self._instances if predicate(i)])

    # ------------------------------------------------------------------
    # Materialisation
    # ------------------------------------------------------------------

    def get(self, *prop_names: str) -> list[dict]:
        """Compute properties for all instances and return a flat list of dicts.

        Each dict has ``path``, ``keys``, and one key per requested property.
        Uses dask to schedule computations respecting property dependencies.
        """
        if not self._instances:
            return []
        return self._get_dask(prop_names)

    def _get_dask(self, prop_names: tuple[str, ...]) -> list[dict]:
        all_delayed: list = []
        # (inst, ordered prop names whose delayed values are in all_delayed)
        structure: list[tuple[AssetInstance, list[str]]] = []

        for inst in self._instances:
            topo = _topo_sort_props(inst.asset, prop_names)
            delayed_by_name: dict[str, object] = {}

            for pname in topo:
                prop = inst.asset._properties.get(pname)
                if prop is None:
                    continue
                dep_delayed = [delayed_by_name[d] for d in prop.requires]
                # dep_delayed items are dask delayed objects; dask resolves them
                # automatically when they appear in argument lists.
                delayed_by_name[pname] = delayed(_compute_prop_cached)(
                    inst, prop, dep_delayed
                )

            requested = [p for p in prop_names if p in delayed_by_name]
            structure.append((inst, requested))
            for pname in requested:
                all_delayed.append(delayed_by_name[pname])

        computed = dask_compute(*all_delayed)

        result = []
        idx = 0
        for inst, requested in structure:
            row: dict = {
                "path": str(inst.path) if inst.path else None,
                "keys": inst.keys,
            }
            for pname in requested:
                row[pname] = computed[idx]
                idx += 1
            result.append(row)
        return result

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Query(asset={self._asset.name!r}, n={len(self._instances)})"
