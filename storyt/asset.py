from __future__ import annotations

import hashlib
import json
import re as re_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from .db import Database
from .instance import AssetInstance
from .property_ import Property
from .query import Query

# ---------------------------------------------------------------------------
# Template helpers for ancestor-path substitution in re= patterns
# ---------------------------------------------------------------------------

# Matches ${name.attr1.attr2...} placeholders
_TEMPLATE_RE = re_module.compile(r"\$\{([^}]+)\}")

# Characters that cannot appear in a literal filesystem path component and
# thus mark the start of the regex portion of a resolved pattern.
_REGEX_SPECIAL = re_module.compile(r"[(*+?\[\\|^$]")


def _has_template(pattern: str) -> bool:
    """Return True if *pattern* contains ``${...}`` placeholders."""
    return "${" in pattern


def _resolve_template(pattern: str, ancestry: dict) -> str:
    """Replace ``${name.attr...}`` placeholders using ancestor instance values.

    *ancestry* maps asset names to their raw instance dicts
    (``{id, path, keys, ...}``).  The expression inside ``${...}`` must start
    with an asset name, followed by zero or more dot-separated
    :class:`pathlib.Path` attribute names, e.g. ``${simulation.path.name}``.
    """

    def replacer(m: re_module.Match) -> str:
        expr = m.group(1)
        parts = expr.split(".")
        asset_name = parts[0]
        attrs = parts[1:]

        inst = ancestry.get(asset_name)
        if inst is None:
            raise ValueError(
                f"No ancestor asset named {asset_name!r} found in template "
                f"(available: {list(ancestry)})"
            )

        # Walk the attribute chain starting from the instance dict.
        # When we encounter a "path" key on a dict, wrap it in Path.
        val: object = inst
        for attr in attrs:
            if isinstance(val, dict):
                raw = val.get(attr)
                val = Path(str(raw)) if attr == "path" and raw is not None else raw
            else:
                val = getattr(val, attr)

        return str(val)

    return _TEMPLATE_RE.sub(replacer, pattern)


def _split_absolute_pattern(resolved: str) -> tuple[Path | None, str]:
    """Split a resolved pattern that begins with ``/`` into *(base_dir, regex)*.

    *base_dir* is the longest leading path whose every component is free of
    regex metacharacters.  *regex* is the remainder.

    Returns ``(None, resolved)`` when the pattern is not absolute.
    """
    if not resolved.startswith("/"):
        return None, resolved

    components = resolved.split("/")
    # components[0] is '' (the empty string before the leading '/')
    static: list[str] = [""]
    idx = 1
    while idx < len(components):
        if _REGEX_SPECIAL.search(components[idx]):
            break
        static.append(components[idx])
        idx += 1

    base_dir = Path("/") if len(static) == 1 else Path("/".join(static))
    remaining = "/".join(components[idx:])
    return base_dir, remaining


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

    def discover(
        self,
        _parent_instances: list[dict] | None = None,
        _ancestry_contexts: list[dict[str, dict]] | None = None,
    ):
        """Scan the filesystem and populate the DB with instances.

        *_ancestry_contexts* is a list parallel to *_parent_instances* where
        each entry maps asset *name* → raw instance dict for every ancestor of
        the corresponding parent instance.  This context is used to resolve
        ``${name.path}`` placeholders in ``re=`` patterns.
        """
        self._ensure_registered()

        if self._parent is not None and self._parent._db_id is not None:
            self._db.register_hierarchy(self._parent._db_id, self._db_id)

        if _parent_instances is None:
            # Root asset: create a single instance at the root path
            root_path = self._get_root_path()
            self._db.register_instance(self._db_id, str(root_path), {}, None)
            my_instances = self._db.get_instances(self._db_id, {})
            # Seed ancestry: root maps to itself
            my_contexts: list[dict[str, dict]] = [
                {self.name: inst} for inst in my_instances
            ]
        elif not _parent_instances:
            my_instances = []
            my_contexts = []
        else:
            if _ancestry_contexts is None:
                _ancestry_contexts = [{} for _ in _parent_instances]

            for parent_inst, ancestry_ctx in zip(
                _parent_instances, _ancestry_contexts, strict=True
            ):
                parent_path = Path(parent_inst["path"]) if parent_inst["path"] else None
                parent_db_id = parent_inst["id"]
                self._create_instances_for_parent(
                    parent_path, parent_db_id, ancestry_ctx
                )

            all_instances = self._db.get_instances(self._db_id, {})
            # Map each parent instance id → its ancestry context so we can
            # propagate the chain to grandchildren.
            parent_id_to_ctx: dict[int, dict[str, dict]] = {
                inst["id"]: ctx
                for inst, ctx in zip(_parent_instances, _ancestry_contexts, strict=True)
            }
            my_instances = all_instances
            my_contexts = [
                {**parent_id_to_ctx.get(inst.get("parent_id"), {}), self.name: inst}
                for inst in all_instances
            ]

        # Recurse into children before registering bindings so that all
        # assets have been registered in object_store by the time we need
        # their IDs for object_binding_member.
        for child in self._children:
            child.discover(
                _parent_instances=my_instances, _ancestry_contexts=my_contexts
            )

        # Register bindings only at the root level (after full tree traversal)
        if _parent_instances is None:
            self._collect_and_register_bindings(set())

    def _create_instances_for_parent(
        self,
        parent_path: Path | None,
        parent_db_id: int,
        ancestry_context: dict[str, dict] | None = None,
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
            if _has_template(self._re_pattern):
                # Resolve ancestor placeholders, then derive the scan root.
                try:
                    resolved = _resolve_template(
                        self._re_pattern, ancestry_context or {}
                    )
                    scan_base, pattern = _split_absolute_pattern(resolved)
                except (ValueError, AttributeError):
                    return
                if scan_base is not None:
                    if scan_base.exists() and scan_base.is_dir():
                        self._scan_for_pattern(scan_base, parent_db_id, pattern)
                else:
                    # Resolved to a relative pattern — fall back to parent path.
                    if (
                        parent_path is not None
                        and parent_path.exists()
                        and parent_path.is_dir()
                    ):
                        self._scan_for_pattern(parent_path, parent_db_id, pattern)
            else:
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

    def _scan_for_pattern(
        self, parent_path: Path, parent_db_id: int, pattern: str | None = None
    ):
        pattern = pattern if pattern is not None else self._re_pattern
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
