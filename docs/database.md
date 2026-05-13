# Database design

`storyt` persists everything to a single SQLite file, stored at
`<root_path>/.storyt.db` (created automatically by `StaticAsset(path=…)`).
All access goes through `storyt/db.py`, which wraps SQLAlchemy 2.0 ORM
sessions.

---

## Schema overview

```
object_store          object_hierarchy
────────────          ────────────────
id (PK)               id (PK)
name                  parent_id ──► object_store.id
pattern               child_id  ──► object_store.id
is_dynamic            UNIQUE(parent_id, child_id)
hash (UNIQUE)
     │
     │ 1:N                    object_instance
     └──────────────────────► ───────────────
                               id (PK)
                               object_id ──► object_store.id
                               path (nullable)
                               keys  (JSON)
                               parent_id ──► object_instance.id (nullable)
                               UNIQUE(object_id, path, keys, parent_id)
                                    │
                                    │ 1:N
                                    ▼
object_property                object_data
───────────────                ───────────
id (PK)                        id (PK)
obj_id ──► object_store.id     obj_property_id ──► object_property.id
name                           obj_instance_id ──► object_instance.id
hash  (source hash)            property_hash
serializer                     data (BLOB)
UNIQUE(obj_id, name)           UNIQUE(obj_property_id, obj_instance_id)

object_property_dep
───────────────────
id (PK)
property_id    ──► object_property.id
depends_on_id  ──► object_property.id
UNIQUE(property_id, depends_on_id)

object_binding          object_binding_member
──────────────          ─────────────────────
id (PK)                 id (PK)
signature (UNIQUE)      binding_id      ──► object_binding.id
                        object_store_id ──► object_store.id
                        key_name
```

---

## Table-by-table reference

### `object_store` — asset types

One row per **asset type** (i.e. per `StaticAsset` node in the hierarchy).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | auto |
| `name` | TEXT | human name, e.g. `"output"` |
| `pattern` | TEXT nullable | the `re=` regex string, or `NULL` for fixed/dynamic |
| `is_dynamic` | INTEGER | `0` = static (filesystem), `1` = dynamic (generator) |
| `hash` | TEXT UNIQUE | SHA-256 of `parent_hash|name|pattern|is_dynamic`; identifies the node position in the tree, not just the name |

**Code path:** `StaticAsset._compute_hash()` → `StaticAsset._ensure_registered()` → `Database.register_asset_type()`.

The hash is computed recursively so two assets with the same name at different
positions in the tree get different rows.

---

### `object_hierarchy` — parent/child type relationships

Records which asset type is a child of which other asset type.

| column | type | notes |
|---|---|---|
| `parent_id` | INTEGER FK→`object_store` | |
| `child_id` | INTEGER FK→`object_store` | |

**Code path:** written once per `(parent, child)` pair during `discover()`:
```python
self._db.register_hierarchy(self._parent._db_id, self._db_id)
```

This is purely informational — instance traversal uses `object_instance.parent_id`
directly and does not query this table at runtime.

---

### `object_instance` — concrete filesystem or dynamic items

One row per discovered **instance** of an asset type: a specific directory,
file, or virtual item produced by a generator.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | auto |
| `object_id` | INTEGER FK→`object_store` | which asset type this is |
| `path` | TEXT nullable | absolute filesystem path; `NULL` for dynamic children |
| `keys` | JSON | named captures from the regex, e.g. `{"iout": "00042"}`, or `{}` |
| `parent_id` | INTEGER FK→`object_instance` nullable | parent instance id; `NULL` for the root |

The `UNIQUE(object_id, path, keys, parent_id)` constraint makes `discover()`
idempotent — running it twice does not duplicate rows.

**Three kinds of instances:**

| kind | `path` | `keys` | `parent_id` |
|---|---|---|---|
| root | absolute path to root dir | `{}` | `NULL` |
| regex match | absolute path to matched entry | regex named groups | parent instance id |
| fixed path | absolute path (base / rel) | `{}` | parent instance id |
| dynamic | `NULL` | `{generator_key: value}` | parent instance id |

**Code path:** `StaticAsset._create_instances_for_parent()` →
`Database.register_instance()`.

---

### `object_property` — property definitions

One row per **named property** registered on an asset type.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | auto |
| `obj_id` | INTEGER FK→`object_store` | which asset type owns this property |
| `name` | TEXT | property name, e.g. `"SFR"` |
| `hash` | TEXT | SHA-256 of the property function's source code (see below) |
| `serializer` | TEXT | `"pickle"` or `"json"` |

The `UNIQUE(obj_id, name)` constraint means there is exactly one property row
per `(asset_type, name)` pair. When the function body changes, `hash` is
updated in place (not a new row) — this allows `object_data` rows to be
invalidated by hash comparison rather than deletion.

**Cache-invalidation hash** (`Property.source_hash()`):
1. Try `inspect.getsource(fn)` — works for named functions and decorated
   functions where source is available.
2. Fall back to `fn.__code__` attributes (`co_consts`, `co_varnames`,
   `co_code`) — covers lambdas defined at the REPL.
3. Last resort: `str(id(fn))` — no invalidation, always unique.

**Code path:** `AssetInstance.__getattr__()` / `_compute_prop_cached()` →
`Database.register_property()`.

---

### `object_data` — cached property values

One row per **computed (property, instance)** pair — the central cache.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | auto |
| `obj_property_id` | INTEGER FK→`object_property` | |
| `obj_instance_id` | INTEGER FK→`object_instance` | |
| `property_hash` | TEXT | source hash **at the time of computation** |
| `data` | BLOB | serialized return value of the property function |

**Cache read:** `get_cached_property(property_id, instance_id, current_hash)`
returns `data` only when `property_hash == current_hash`; otherwise returns
`None` (stale / never computed).

**Cache write:** `set_cached_property(…)` upserts — if a row already exists
it overwrites `data` and `property_hash` in place.

**Code path:** `AssetInstance.__getattr__()` calls `get_cached_property`; on
a miss it calls the function and then `set_cached_property`.  The same logic
lives in `_compute_prop_cached()` for the dask path.

---

### `object_property_dep` — property dependency graph

One row per directed `(property → dependency)` edge.

| column | type | notes |
|---|---|---|
| `property_id` | INTEGER FK→`object_property` | the dependent |
| `depends_on_id` | INTEGER FK→`object_property` | the dependency |

This mirrors the `requires=[…]` list passed to `add_property()`.

Currently the table is written by `Database.register_property_dep()` and read
by `Database.get_property_dep_ids()`, but the in-memory `prop.requires` list
is the authoritative source for scheduling; the DB copy is available for
inspection or future query-planning use.

---

### `object_binding` + `object_binding_member` — cross-asset key bindings

`bind((output, "iout"), (halo_catalogue, "iout"))` means: an `output` instance
with `keys["iout"] == "X"` is related to the `halo_catalogue` instance(s) that
also have `keys["iout"] == "X"`.

**`object_binding`** — one row per binding declaration:

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `signature` | TEXT UNIQUE | SHA-256 of the sorted `[(obj_id, key_name), …]` list — deduplicates re-registration |

**`object_binding_member`** — one row per participating asset/key pair:

| column | type | notes |
|---|---|---|
| `binding_id` | FK→`object_binding` | |
| `object_store_id` | FK→`object_store` | |
| `key_name` | TEXT | the key name in `object_instance.keys` to match on |

**Resolution at query time** (`Database.get_bound_instances(instance_id, target_object_id)`):
1. Look up the source instance's `object_id` and `keys`.
2. Find all bindings that include `object_id` as a member.
3. For each such binding, extract the key value from the source instance.
4. Find the target's member key name.
5. Query `object_instance` for target instances whose `keys[target_key] == value`.

**Code path:** `bind()` in `binding.py` stores the binding in-memory on each
participating `StaticAsset._bindings` list.  After the full tree has been
traversed by `discover()`, `_collect_and_register_bindings()` deduplicates and
calls `Database.register_binding()`.

---

## Data lifecycle

```
StaticAsset.discover()
│
├─ register_asset_type()         → object_store row
├─ register_hierarchy()          → object_hierarchy row
├─ register_instance()  (×N)     → object_instance rows
│     (regex scan / fixed paths / generator)
└─ register_binding()   (×M)     → object_binding + object_binding_member rows


Query.get("prop_a", "prop_b")
│
├─ _topo_sort_props()            in-memory DFS on prop.requires
│
└─ for each instance:
     └─ delayed(_compute_prop_cached)(inst, prop, dep_delayed)
          │
          ├─ register_property()        → object_property row (upsert)
          ├─ get_cached_property()      → object_data lookup (hash check)
          │    hit  → deserialize → return
          │    miss ↓
          ├─ prop.compute(inst, deps)   → call user function
          └─ set_cached_property()      → object_data upsert
```

---

## Concurrency

All writes use an optimistic **check-then-insert** pattern followed by an
`IntegrityError` rollback-and-re-read, which is correct for both the threaded
and process schedulers:

```python
try:
    session.add(obj)
    session.commit()
except IntegrityError:
    session.rollback()
    existing = session.query(…).filter_by(…).first()
    return existing.id
```

SQLite write serialisation is handled by SQLite itself when using a file-based
DB (WAL or deferred locks).  The engine is created with
`connect_args={"check_same_thread": False}` to allow multiple threads to share
one connection pool.

For the **processes** scheduler, `Database.__getstate__` / `__setstate__`
serialise only the file path; each worker process creates its own engine and
connection pool pointing at the same `.storyt.db` file.  In-memory databases
raise a `TypeError` at pickle time because they cannot be shared across
process boundaries.
