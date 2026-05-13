from __future__ import annotations

import fnmatch
import hashlib
import json
import re as re_module
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from .db import Database
from .instance import AssetInstance
from .logging import logger
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
                if attr == "path" and raw is not None:
                    val = Path(str(raw))
                else:
                    val = raw
            else:
                val = getattr(val, attr)

        return str(val)

    return _TEMPLATE_RE.sub(replacer, pattern)


def _split_absolute_pattern(resolved: str) -> tuple[Path | None, str]:
    """Split an absolute pattern into a base directory and remainder.

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


def _looks_like_regex(pattern: str) -> bool:
    return bool(re_module.search(r"\(\?P<|[()\\|^$+{}]", pattern))


def _match_path_component(pattern: str, value: str) -> tuple[bool, dict[str, str]]:
    """Match a single path component as regex when possible, otherwise glob."""
    regex_is_plausible = not any(ch in pattern for ch in "*?[")
    if regex_is_plausible or _looks_like_regex(pattern):
        try:
            match = re_module.fullmatch(pattern, value)
        except re_module.error:
            match = None
        if match is not None:
            groups = match.groupdict()
            return True, {key: val for key, val in groups.items() if val is not None}
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(value, pattern), {}
    return pattern == value, {}


def _scan_component_path(
    asset: StaticAsset,
    current_path: Path,
    components: list[str],
    parent_db_id: int,
    asset_id: int,
    keys: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Recursively scan one component at a time."""
    if not components:
        return 0, 0

    head = components[0]
    tail = components[1:]
    scanned = 0
    matched = 0

    try:
        entries = current_path.iterdir()
    except (NotADirectoryError, PermissionError):
        return 0, 0

    for entry in entries:
        scanned += 1
        is_match, extracted = _match_path_component(head, entry.name)
        if not is_match:
            continue

        next_keys = dict(keys or {})
        next_keys.update(extracted)

        if tail:
            if entry.is_dir():
                sub_scanned, sub_matched = _scan_component_path(
                    asset,
                    entry,
                    tail,
                    parent_db_id,
                    asset_id,
                    next_keys,
                )
                scanned += sub_scanned
                matched += sub_matched
        else:
            asset._db.register_instance(asset_id, str(entry), next_keys, parent_db_id)
            matched += 1

    return scanned, matched


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
        self._readers: dict[str, tuple[Callable, list[str]]] = {}
        self._default_reader_name: str | None = None
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

    @staticmethod
    def _normalize_requires(requires: str | list[str] | None) -> list[str]:
        if requires is None:
            return []
        if isinstance(requires, str):
            return [requires]
        return list(requires)

    def _has_reader(self, name: str) -> bool:
        return name in self._readers

    def _resolve_default_reader(self) -> str:
        if self._default_reader_name is None:
            raise RuntimeError(f"No reader defined for asset '{self.name}'")
        return self._default_reader_name

    def _load_reader_for_path(self, path: Path, reader_name: str | None = None):
        name = reader_name or self._resolve_default_reader()
        if name not in self._readers:
            raise RuntimeError(
                f"Reader '{name}' is not registered for asset '{self.name}'"
            )
        from .instance import AssetInstance

        temp = AssetInstance(self, -1, path, {}, None)
        return temp.reader[name]

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

    def register_reader(
        self,
        fn: Callable | None = None,
        *,
        name: str | None = None,
        requires: str | list[str] | None = None,
    ):
        """Register a named reader.

        Can be used as:
        - register_reader(lambda inst: ..., name="df")
        - @register_reader
        - @register_reader(requires="other_reader")
        """

        deps = self._normalize_requires(requires)

        def decorator(f: Callable) -> Callable:
            reader_name = name
            if reader_name is None:
                if f.__name__ == "<lambda>":
                    raise ValueError("A lambda reader requires an explicit name=")
                reader_name = f.__name__

            for dep in deps:
                if dep not in self._readers:
                    raise ValueError(
                        f"Reader '{reader_name}' depends on unknown reader '{dep}'"
                    )

            self._readers[reader_name] = (f, deps)
            if self._default_reader_name is None:
                self._default_reader_name = reader_name
            return f

        if fn is None:
            return decorator
        return decorator(fn)

    @property
    def reader(self):
        return self._readers

    def add_property(
        self,
        name: str,
        fn=None,
        requires=None,
        serializer: str = "pickle",
        reader: str | None = None,
    ):
        """Register a computed property (can be used as a decorator)."""
        req = self._normalize_requires(requires)
        if reader is not None and not self._has_reader(reader):
            raise ValueError(
                f"Unknown reader '{reader}' for property '{name}' on asset '{self.name}'"
            )

        if fn is None:

            def decorator(f: Callable) -> Callable:
                self._properties[name] = Property(
                    name,
                    f,
                    serializer=serializer,
                    requires=req,
                    reader=reader,
                )
                return f

            return decorator
        self._properties[name] = Property(
            name,
            fn,
            serializer=serializer,
            requires=req,
            reader=reader,
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
        force: bool = False,
    ):
        """Scan the filesystem and populate the DB with instances.

        *_ancestry_contexts* is a list parallel to *_parent_instances* where
        each entry maps asset *name* → raw instance dict for every ancestor of
        the corresponding parent instance.  This context is used to resolve
        ``${name.path}`` placeholders in ``re=`` patterns.
        If force is True, always rescan and update timestamps.
        """
        import time

        depth = 0 if _parent_instances is None else self._depth()
        indent = "  " * depth
        asset_id = self._ensure_registered()

        # Top-level discover batches all DB writes into one commit.
        group_ctx = (
            self._db.group_operations() if _parent_instances is None else nullcontext()
        )
        with group_ctx:
            logger.debug("%sdiscover[%s] start", indent, self.name)
            logger.debug("%sdiscover[%s] registered", indent, self.name)

            if self._parent is not None and self._parent._db_id is not None:
                self._db.register_hierarchy(self._parent._db_id, asset_id)
                logger.debug("%sdiscover[%s] hierarchy linked", indent, self.name)

            if _parent_instances is None:
                # Root asset: create a single instance at the root path
                root_path = self._get_root_path()
                mtime = (
                    int(root_path.stat().st_mtime)
                    if root_path.exists()
                    else int(time.time())
                )
                self._db.register_instance(
                    asset_id, str(root_path), {}, None, timestamp=mtime
                )
                logger.debug(
                    "%sdiscover[%s] root instance registered",
                    indent,
                    self.name,
                )
                my_instances = self._db.get_instances(asset_id, {})
                logger.debug(
                    "%sdiscover[%s] instances loaded=%d",
                    indent,
                    self.name,
                    len(my_instances),
                )
                # Seed ancestry: root maps to itself
                my_contexts: list[dict[str, dict]] = [
                    {self.name: inst} for inst in my_instances
                ]
            elif not _parent_instances:
                my_instances = []
                my_contexts = []
                logger.debug(
                    "%sdiscover[%s] skipped (no parent instances)",
                    indent,
                    self.name,
                )
            else:
                if _ancestry_contexts is None:
                    _ancestry_contexts = [{} for _ in _parent_instances]

                existing_rows = (
                    self._db.get_instances(asset_id, {}) if not force else []
                )
                by_parent_path: dict[tuple[int, str], int] = {}
                by_parent_max_ts: dict[int, int] = {}
                for row in existing_rows:
                    parent_id = row.get("parent_id")
                    ts = row.get("timestamp")
                    path = row.get("path")
                    if isinstance(parent_id, int) and isinstance(ts, int):
                        prev_max = by_parent_max_ts.get(parent_id)
                        if prev_max is None or ts > prev_max:
                            by_parent_max_ts[parent_id] = ts
                        if isinstance(path, str):
                            by_parent_path[parent_id, path] = ts

                for parent_inst, ancestry_ctx in zip(
                    _parent_instances, _ancestry_contexts, strict=True
                ):
                    parent_path = (
                        Path(parent_inst["path"]) if parent_inst["path"] else None
                    )
                    parent_db_id = parent_inst["id"]
                    # Check DB for existing instance and timestamp
                    if parent_path and parent_path.exists():
                        mtime = int(parent_path.stat().st_mtime)
                    else:
                        mtime = int(time.time())
                    # If not force, check DB timestamp
                    skip = False
                    if not force:
                        # For normal assets, compare by concrete child path.
                        if parent_path is not None:
                            dbts = by_parent_path.get((parent_db_id, str(parent_path)))
                            if dbts is not None and dbts >= mtime:
                                skip = True
                        # For dynamic/pathless assets, compare max child ts by parent.
                        if not skip and self._is_dynamic:
                            parent_max_ts = by_parent_max_ts.get(parent_db_id)
                            if parent_max_ts is not None and parent_max_ts >= mtime:
                                skip = True
                    if skip:
                        logger.debug(
                            "%sdiscover[%s] skipping unchanged instance %s",
                            indent,
                            self.name,
                            parent_path,
                        )
                        continue
                    self._create_instances_for_parent(
                        parent_path,
                        parent_db_id,
                        ancestry_ctx,
                        _depth=depth + 1,
                        _mtime=mtime,
                        _force=force,
                    )
                logger.debug(
                    "%sdiscover[%s] parent scans=%d",
                    indent,
                    self.name,
                    len(_parent_instances),
                )

                all_instances = self._db.get_instances(asset_id, {})
                logger.debug(
                    "%sdiscover[%s] instances loaded=%d",
                    indent,
                    self.name,
                    len(all_instances),
                )
                # Map each parent instance id → its ancestry context so we can
                # propagate the chain to grandchildren.
                parent_id_to_ctx: dict[int, dict[str, dict]] = {
                    inst["id"]: ctx
                    for inst, ctx in zip(
                        _parent_instances,
                        _ancestry_contexts,
                        strict=True,
                    )
                }
                my_instances = all_instances
                my_contexts = []
                for inst in all_instances:
                    parent_id = inst.get("parent_id")
                    inherited = (
                        parent_id_to_ctx.get(parent_id, {})
                        if isinstance(parent_id, int)
                        else {}
                    )
                    my_contexts.append({**inherited, self.name: inst})

            # Recurse into children before registering bindings so that all
            # assets have been registered in object_store by the time we need
            # their IDs for object_binding_member.
            for child in self._children:
                child.discover(
                    _parent_instances=my_instances,
                    _ancestry_contexts=my_contexts,
                    force=force,
                )

            # Register bindings only at the root level (after full tree traversal)
            if _parent_instances is None:
                self._collect_and_register_bindings(set())
                logger.debug("%sdiscover[%s] bindings collected", indent, self.name)

            logger.debug("%sdiscover[%s] done", indent, self.name)

    def _depth(self) -> int:
        depth = 0
        node = self._parent
        while node is not None:
            depth += 1
            node = node._parent
        return depth

    def _create_instances_for_parent(
        self,
        parent_path: Path | None,
        parent_db_id: int,
        ancestry_context: dict[str, dict] | None = None,
        *,
        _depth: int = 0,
        _mtime: int | None = None,
        _force: bool = False,
    ):
        indent = "  " * _depth
        asset_id = self._ensure_registered()
        if self._is_dynamic:
            if self._parent is not None and parent_path:
                created = 0
                try:
                    data = self._parent._load_reader_for_path(parent_path)
                    entries: list[dict] = []
                    for key_val, _item in self._generator(data):
                        entries.append(
                            {
                                "path": None,
                                "keys": {self._generator_key: str(key_val)},
                                "timestamp": _mtime,
                            }
                        )
                    created = self._db.register_instances_bulk(
                        asset_id,
                        parent_db_id,
                        entries,
                    )
                    logger.debug(
                        "%sdynamic[%s] parent_id=%d created=%d",
                        indent,
                        self.name,
                        parent_db_id,
                        created,
                    )
                except Exception as e:
                    logger.warning(
                        "%sdynamic[%s] parent_id=%d failed",
                        indent,
                        self.name,
                        parent_db_id,
                    )
                    for line in str(e).splitlines():
                        logger.error("%s>  %s", indent, line)

        elif self._re_pattern is not None:
            if _has_template(self._re_pattern):
                # Resolve ancestor placeholders, then derive the scan root.
                try:
                    resolved = _resolve_template(
                        self._re_pattern, ancestry_context or {}
                    )
                    scan_base, pattern = _split_absolute_pattern(resolved)
                    logger.debug("%sregex[%s] template resolved", indent, self.name)
                except (ValueError, AttributeError) as e:
                    logger.warning(
                        "%sregex[%s] template resolution failed for parent_id=%d",
                        indent,
                        self.name,
                        parent_db_id,
                    )
                    for line in str(e).splitlines():
                        logger.error("%s>  %s", indent, line)
                    return
                if scan_base is not None:
                    if scan_base.exists() and scan_base.is_dir():
                        self._scan_for_pattern(
                            scan_base,
                            parent_db_id,
                            pattern,
                            _depth=_depth,
                            _force=_force,
                        )
                    else:
                        logger.warning(
                            "%sregex[%s] scan base missing: %s",
                            indent,
                            self.name,
                            scan_base,
                        )
                else:
                    # Resolved to a relative pattern — fall back to parent path.
                    if (
                        parent_path is not None
                        and parent_path.exists()
                        and parent_path.is_dir()
                    ):
                        self._scan_for_pattern(
                            parent_path,
                            parent_db_id,
                            pattern,
                            _depth=_depth,
                            _force=_force,
                        )
            else:
                if (
                    parent_path is not None
                    and parent_path.exists()
                    and parent_path.is_dir()
                ):
                    self._scan_for_pattern(
                        parent_path,
                        parent_db_id,
                        _depth=_depth,
                        _force=_force,
                    )

        elif self._fixed_paths is not None:
            base = parent_path if parent_path is not None else self._get_root_path()
            for rel in self._fixed_paths:
                full_path = base / rel
                mtime = int(full_path.stat().st_mtime) if full_path.exists() else None
                self._db.register_instance(
                    asset_id, str(full_path), {}, parent_db_id, timestamp=mtime
                )
            logger.debug(
                "%sfixed[%s] parent_id=%d created=%d",
                indent,
                self.name,
                parent_db_id,
                len(self._fixed_paths),
            )

    def _scan_for_pattern(
        self,
        parent_path: Path,
        parent_db_id: int,
        pattern: str | None = None,
        *,
        _depth: int = 0,
        _force: bool = False,
    ):
        import time

        indent = "  " * _depth
        asset_id = self._ensure_registered()
        pattern = pattern if pattern is not None else self._re_pattern
        assert pattern is not None

        components = [component for component in pattern.split("/") if component]
        scanned = 0
        matched = 0
        try:
            entries = list(parent_path.iterdir())
        except (NotADirectoryError, PermissionError) as e:
            logger.warning("%sscan[%s] cannot list %s", indent, self.name, parent_path)
            for line in str(e).splitlines():
                logger.error("%s>  %s", indent, line)
            return
        # Preload all DB instances for this asset and parent
        db_rows = self._db.get_instances(asset_id, {}) if not _force else []
        db_index = {(row["parent_id"], row["path"]): row for row in db_rows}
        for entry in entries:
            mtime = int(entry.stat().st_mtime) if entry.exists() else int(time.time())

            db_row = db_index.get((parent_db_id, str(entry))) if not _force else None
            if not _force and db_row is not None:
                dbts = db_row.get("timestamp")
                if dbts is not None and dbts >= mtime:
                    # If this is a directory and we have more components, skip recursing
                    if len(components) > 1 and entry.is_dir():
                        logger.debug(
                            "%sscan[%s] skipping unchanged subtree %s",
                            indent,
                            self.name,
                            entry,
                        )
                        scanned += 1
                        continue
                    # If this is a file/leaf, skip registration
                    if len(components) == 1:
                        logger.debug(
                            "%sscan[%s] skipping unchanged %s", indent, self.name, entry
                        )
                        scanned += 1
                        continue
            # Match pattern
            is_match, extracted = _match_path_component(components[0], entry.name)
            if not is_match:
                continue
            next_keys = dict(extracted)
            if len(components) > 1 and entry.is_dir():
                # Recurse only if not skipped above
                sub_pattern = "/".join(components[1:])
                self._scan_for_pattern(
                    entry,
                    parent_db_id,
                    sub_pattern,
                    _depth=_depth + 1,
                    _force=_force,
                )
            else:
                self._db.register_instance(
                    asset_id, str(entry), next_keys, parent_db_id, timestamp=mtime
                )
                matched += 1
            scanned += 1
        logger.debug(
            "%sscan[%s] parent_id=%d scanned=%d matched=%d",
            indent,
            self.name,
            parent_db_id,
            scanned,
            matched,
        )

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
