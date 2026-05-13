import hashlib
import json
import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            path = str(Path(path).resolve())
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self.ensure_schema()

    def ensure_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS object_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pattern TEXT,
                is_dynamic BOOLEAN DEFAULT 0,
                hash TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS object_hierarchy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES object_store(id),
                child_id INTEGER NOT NULL REFERENCES object_store(id),
                UNIQUE(parent_id, child_id)
            );

            CREATE TABLE IF NOT EXISTS object_instance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_id INTEGER NOT NULL REFERENCES object_store(id),
                path TEXT,
                keys TEXT NOT NULL DEFAULT '{}',
                parent_id INTEGER REFERENCES object_instance(id)
            );

            CREATE TABLE IF NOT EXISTS object_property (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obj_id INTEGER NOT NULL REFERENCES object_store(id),
                name TEXT NOT NULL,
                hash TEXT NOT NULL,
                serializer TEXT NOT NULL DEFAULT 'pickle',
                UNIQUE(obj_id, name)
            );

            CREATE TABLE IF NOT EXISTS object_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obj_property_id INTEGER NOT NULL REFERENCES object_property(id),
                obj_instance_id INTEGER NOT NULL REFERENCES object_instance(id),
                property_hash TEXT NOT NULL,
                data BLOB,
                UNIQUE(obj_property_id, obj_instance_id)
            );

            CREATE TABLE IF NOT EXISTS object_binding (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature TEXT UNIQUE
            );

            CREATE TABLE IF NOT EXISTS object_binding_member (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                binding_id INTEGER NOT NULL REFERENCES object_binding(id),
                object_store_id INTEGER NOT NULL REFERENCES object_store(id),
                key_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS object_property_dep (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL REFERENCES object_property(id),
                depends_on_id INTEGER NOT NULL REFERENCES object_property(id),
                UNIQUE(property_id, depends_on_id)
            );
        """)
        self.conn.commit()

    def register_asset_type(
        self, name: str, pattern: str | None, is_dynamic: bool, hash_: str
    ) -> int:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO object_store (name, pattern, is_dynamic, hash) VALUES (?, ?, ?, ?)",
                (name, pattern, int(is_dynamic), hash_),
            )
            self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM object_store WHERE hash = ?", (hash_,)
        ).fetchone()
        return row["id"]

    def register_hierarchy(self, parent_id: int, child_id: int):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO object_hierarchy (parent_id, child_id) VALUES (?, ?)",
                (parent_id, child_id),
            )
            self.conn.commit()

    def register_instance(
        self,
        object_id: int,
        path: str | None,
        keys: dict,
        parent_id: int | None,
    ) -> int:
        keys_json = json.dumps(keys, sort_keys=True)

        # Check for existing instance (handle NULL path carefully)
        if path is None:
            if parent_id is None:
                existing = self.conn.execute(
                    "SELECT id FROM object_instance "
                    "WHERE object_id = ? AND path IS NULL AND keys = ? AND parent_id IS NULL",
                    (object_id, keys_json),
                ).fetchone()
            else:
                existing = self.conn.execute(
                    "SELECT id FROM object_instance "
                    "WHERE object_id = ? AND path IS NULL AND keys = ? AND parent_id = ?",
                    (object_id, keys_json, parent_id),
                ).fetchone()
        else:
            existing = self.conn.execute(
                "SELECT id FROM object_instance WHERE object_id = ? AND path = ? AND keys = ?",
                (object_id, path, keys_json),
            ).fetchone()

        if existing:
            return existing["id"]

        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO object_instance (object_id, path, keys, parent_id) VALUES (?, ?, ?, ?)",
                (object_id, path, keys_json, parent_id),
            )
            self.conn.commit()
        return cur.lastrowid

    def register_property(
        self, obj_id: int, name: str, source_hash: str, serializer: str = "pickle"
    ) -> int:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO object_property (obj_id, name, hash, serializer) VALUES (?, ?, ?, ?)",
                (obj_id, name, source_hash, serializer),
            )
            self.conn.execute(
                "UPDATE object_property SET hash = ? WHERE obj_id = ? AND name = ?",
                (source_hash, obj_id, name),
            )
            self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM object_property WHERE obj_id = ? AND name = ?",
            (obj_id, name),
        ).fetchone()
        return row["id"]

    def get_cached_property(
        self, property_id: int, instance_id: int, current_hash: str
    ) -> bytes | None:
        row = self.conn.execute(
            "SELECT data, property_hash FROM object_data "
            "WHERE obj_property_id = ? AND obj_instance_id = ?",
            (property_id, instance_id),
        ).fetchone()
        if row is None:
            return None
        if row["property_hash"] != current_hash:
            return None
        return bytes(row["data"])

    def set_cached_property(
        self, property_id: int, instance_id: int, data: bytes, hash_: str
    ):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO object_data "
                "(obj_property_id, obj_instance_id, property_hash, data) VALUES (?, ?, ?, ?)",
                (property_id, instance_id, hash_, data),
            )
            self.conn.commit()

    def register_property_dep(self, property_id: int, depends_on_id: int):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO object_property_dep (property_id, depends_on_id) VALUES (?, ?)",
                (property_id, depends_on_id),
            )
            self.conn.commit()

    def get_property_dep_ids(self, property_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT depends_on_id FROM object_property_dep WHERE property_id = ?",
            (property_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_child_instances(
        self, parent_instance_id: int, child_object_id: int
    ) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM object_instance WHERE parent_id = ? AND object_id = ?",
            (parent_instance_id, child_object_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_instances(self, object_id: int, key_filters: dict) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM object_instance WHERE object_id = ?", (object_id,)
        ).fetchall()
        result = []
        for row in rows:
            keys = json.loads(row["keys"])
            if all(keys.get(k) == str(v) for k, v in key_filters.items()):
                result.append(dict(row))
        return result

    def get_bound_instances(
        self, instance_id: int, target_object_id: int
    ) -> list[dict]:
        inst = self.conn.execute(
            "SELECT object_id, keys FROM object_instance WHERE id = ?", (instance_id,)
        ).fetchone()
        if inst is None:
            return []

        source_object_id = inst["object_id"]
        source_keys = json.loads(inst["keys"])

        bindings = self.conn.execute(
            """
            SELECT ob.id as binding_id, obm.key_name as source_key
            FROM object_binding ob
            JOIN object_binding_member obm ON obm.binding_id = ob.id
            WHERE obm.object_store_id = ?
            """,
            (source_object_id,),
        ).fetchall()

        result = []
        seen_ids: set[int] = set()

        for binding_row in bindings:
            binding_id = binding_row["binding_id"]
            source_key = binding_row["source_key"]

            target_members = self.conn.execute(
                "SELECT key_name FROM object_binding_member "
                "WHERE binding_id = ? AND object_store_id = ?",
                (binding_id, target_object_id),
            ).fetchall()

            for target_member in target_members:
                target_key = target_member["key_name"]
                key_value = source_keys.get(source_key)
                if key_value is None:
                    continue
                for ti in self.get_instances(target_object_id, {target_key: key_value}):
                    if ti["id"] not in seen_ids:
                        seen_ids.add(ti["id"])
                        result.append(ti)

        return result

    def register_binding(self, members: list[tuple]) -> int:
        """Register a binding. members is [(object_store_id, key_name), ...]."""
        sig = hashlib.sha256(
            json.dumps(sorted(members), sort_keys=True).encode()
        ).hexdigest()

        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO object_binding (signature) VALUES (?)", (sig,)
            )
            self.conn.commit()

        row = self.conn.execute(
            "SELECT id FROM object_binding WHERE signature = ?", (sig,)
        ).fetchone()
        binding_id = row["id"]

        existing_members = self.conn.execute(
            "SELECT object_store_id, key_name FROM object_binding_member WHERE binding_id = ?",
            (binding_id,),
        ).fetchall()
        if not existing_members:
            with self._lock:
                for obj_id, key_name in members:
                    self.conn.execute(
                        "INSERT INTO object_binding_member (binding_id, object_store_id, key_name) "
                        "VALUES (?, ?, ?)",
                        (binding_id, obj_id, key_name),
                    )
                self.conn.commit()

        return binding_id
