from __future__ import annotations

import hashlib
import json
import re as re_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .query import Query

from .db import Database
from .property_ import Property


class StaticAsset:
    def __init__(
        self,
        *,
        path: str | Path | None = None,
        name: str,
        _db: Database | None = None,
        _parent: StaticAsset | None = None,
        _re_pattern: str | None = None,
        _is_dynamic: bool = False,
        _generator: Callable | None = None,
        _generator_key: str | None = None,
        _fixed_paths: list[str] | None = None,
    ):
        self.name = name
        self._parent = _parent
        self._re_pattern = _re_pattern
        self._is_dynamic = _is_dynamic
        self._generator = _generator
        self._generator_key = _generator_key
        self._fixed_paths = _fixed_paths
        self._children: list[StaticAsset] = []
        self._reader: Callable | None = None
        self._properties: dict[str, Property] = {}
        self._bindings: list[list[tuple[StaticAsset, str]]] = []
        self._db_id: int | None = None
        self._root_path: Path | None = None

        if path is not None:
            self._root_path = Path(path).resolve()
            db_path = self._root_path / ".storyt.db"
            self._db: Database = Database(db_path)
        else:
            self._db = _db  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_root_path(self) -> Path:
        if self._root_path is not None:
            return self._root_path
        if self._parent is not None:
            return self._parent._get_root_path()
        raise RuntimeError("No root path defined")

    def _compute_hash(self) -> str:
        parent_hash = self._parent._compute_hash() if self._parent else ""
        data = f"{parent_hash}|{self.name}|{self._re_pattern}|{int(self._is_dynamic)}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _ensure_registered(self) -> int:
        if self._db_id is not None:
            return self._db_id
        h = self._compute_hash()
        self._db_id = self._db.register_asset_type(
            self.name, self._re_pattern, self._is_dynamic, h
        )
        return self._db_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_children(
        self,
        *args,
        re: str | None = None,
        path: str | list[str] | None = None,
        name: str,
        key: str | None = None,
    ) -> StaticAsset:
        if args and callable(args[0]):
            child = StaticAsset(
                name=name,
                _db=self._db,
                _parent=self,
                _is_dynamic=True,
                _generator=args[0],
                _generator_key=key,
            )
        elif re is not None:
            child = StaticAsset(
                name=name,
                _db=self._db,
                _parent=self,
                _re_pattern=re,
            )
        elif path is not None:
            normalized = [path] if isinstance(path, str) else list(path)
            child = StaticAsset(
                name=name,
                _db=self._db,
                _parent=self,
                _fixed_paths=normalized,
            )
        else:
            raise ValueError(
                "add_children requires re=, path=, or a callable as the first argument"
            )
        self._children.append(child)
        return child

    def reader(self, fn: Callable) -> Callable:
        """Set the reader function for this asset."""
        self._reader = fn
        return fn

    def add_property(
        self, name: str, fn=None, requires=None, serializer: str = "pickle"
    ):
        """Register a computed property (can be used as a decorator)."""
        if fn is None:

            def decorator(f: Callable) -> Callable:
                self._properties[name] = Property(
                    name, f, serializer=serializer, requires=requires
                )
                return f

            return decorator
        self._properties[name] = Property(
            name, fn, serializer=serializer, requires=requires
        )

    def all(self) -> Query:
        """Return a Query over all instances of this asset."""
        from .query import Query

        return Query(self, self.instances())

    def __getattr__(self, name: str) -> StaticAsset:
        """Allow attribute-style access to child assets by name."""
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            children = object.__getattribute__(self, "_children")
        except AttributeError as err:
            raise AttributeError(name) from err
        for child in children:
            if child.name == name:
                return child
        raise AttributeError(f"StaticAsset {self.name!r} has no child named {name!r}")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, _parent_instances: list[dict] | None = None):
        """Scan the filesystem and populate the DB with instances."""
        self._ensure_registered()

        if self._parent is not None and self._parent._db_id is not None:
            self._db.register_hierarchy(self._parent._db_id, self._db_id)

        if _parent_instances is None:
            # Root asset: create a single instance at the root path
            root_path = self._get_root_path()
            self._db.register_instance(self._db_id, str(root_path), {}, None)
            my_instances = self._db.get_instances(self._db_id, {})
        elif not _parent_instances:
            my_instances = []
        else:
            for parent_inst in _parent_instances:
                parent_path = Path(parent_inst["path"]) if parent_inst["path"] else None
                parent_db_id = parent_inst["id"]
                self._create_instances_for_parent(parent_path, parent_db_id)
            my_instances = self._db.get_instances(self._db_id, {})

        # Recurse into children before registering bindings so that all
        # assets have been registered in object_store by the time we need
        # their IDs for object_binding_member.
        for child in self._children:
            child.discover(_parent_instances=my_instances)

        # Register bindings only at the root level (after full tree traversal)
        if _parent_instances is None:
            self._collect_and_register_bindings(set())

    def _create_instances_for_parent(
        self, parent_path: Path | None, parent_db_id: int
    ):
        if self._is_dynamic:
            if self._parent and self._parent._reader and parent_path:
                try:
                    data = self._parent._reader(parent_path)
                    for key_val, _item in self._generator(data):  # type: ignore[misc]
                        keys = {self._generator_key: str(key_val)}  # type: ignore[index]
                        self._db.register_instance(
                            self._db_id, None, keys, parent_db_id
                        )
                except Exception:
                    pass

        elif self._re_pattern is not None:
            if (
                parent_path is not None
                and parent_path.exists()
                and parent_path.is_dir()
            ):
                self._scan_for_pattern(parent_path, parent_db_id)

        elif self._fixed_paths is not None:
            base = parent_path if parent_path is not None else self._get_root_path()
            for rel in self._fixed_paths:
                full_path = base / rel
                self._db.register_instance(
                    self._db_id, str(full_path), {}, parent_db_id
                )

    def _scan_for_pattern(self, parent_path: Path, parent_db_id: int):
        pattern = self._re_pattern
        assert pattern is not None

        if "/" in pattern:
            # Pattern spans multiple path components — walk recursively
            try:
                for entry in parent_path.rglob("*"):
                    try:
                        rel = str(entry.relative_to(parent_path))
                        m = re_module.fullmatch(pattern, rel)
                        if m:
                            keys = {
                                k: v for k, v in m.groupdict().items() if v is not None
                            }
                            self._db.register_instance(
                                self._db_id, str(entry), keys, parent_db_id
                            )
                    except ValueError:
                        pass
            except PermissionError:
                pass
        else:
            try:
                for entry in parent_path.iterdir():
                    m = re_module.fullmatch(pattern, entry.name)
                    if m:
                        keys = {k: v for k, v in m.groupdict().items() if v is not None}
                        self._db.register_instance(
                            self._db_id, str(entry), keys, parent_db_id
                        )
            except (NotADirectoryError, PermissionError):
                pass

    def _collect_and_register_bindings(self, registered: set):
        """Walk the tree, deduplicate, and register all pending bindings."""
        for binding in self._bindings:
            # Canonical key: frozenset of (asset_hash, key_name) pairs
            key = frozenset((asset._compute_hash(), kname) for asset, kname in binding)
            if key not in registered:
                registered.add(key)
                members = [
                    (asset._ensure_registered(), kname) for asset, kname in binding
                ]
                self._db.register_binding(members)

        for child in self._children:
            child._collect_and_register_bindings(registered)

    # ------------------------------------------------------------------
    # Instance access
    # ------------------------------------------------------------------

    def instances(self, **key_filters) -> list:
        from .instance import AssetInstance

        if self._db_id is None:
            return []
        rows = self._db.get_instances(self._db_id, key_filters)
        result = []
        for row in rows:
            keys = (
                json.loads(row["keys"]) if isinstance(row["keys"], str) else row["keys"]
            )
            path = Path(row["path"]) if row["path"] else None
            result.append(
                AssetInstance(self, row["id"], path, keys, row.get("parent_id"))
            )
        return result

    def __repr__(self) -> str:
        return (
            f"StaticAsset(name={self.name!r}, "
            f"pattern={self._re_pattern!r}, "
            f"fixed_paths={self._fixed_paths!r})"
        )
