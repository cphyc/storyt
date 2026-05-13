import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, relationship


class Base(DeclarativeBase):
    pass


class SQLResultWrapper:
    """Wraps SQLAlchemy result to provide dict-like row access."""

    def __init__(self, result):
        self._result = result

    def fetchall(self):
        """Fetch all rows as dict-like objects."""
        return [
            dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
            for row in self._result
        ]

    def fetchone(self):
        """Fetch one row as dict-like object."""
        row = self._result.first()
        if row is None:
            return None
        return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)

    def __iter__(self):
        """Iterate over rows as dict-like objects."""
        return iter(
            [
                dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                for row in self._result
            ]
        )


class SQLConnectionWrapper:
    """Wraps SQLAlchemy connection to support raw SQL strings for backward compatibility."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql_string, params=None):
        """Execute raw SQL string and return a result proxy."""
        if isinstance(sql_string, str):
            sql_string = text(sql_string)
        if params:
            result = self._conn.execute(sql_string, params)
        else:
            result = self._conn.execute(sql_string)
        return SQLResultWrapper(result)

    def __getattr__(self, name):
        """Delegate other attributes to the wrapped connection."""
        return getattr(self._conn, name)


class ObjectStore(Base):
    __tablename__ = "object_store"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    pattern = Column(String, nullable=True)
    is_dynamic = Column(Integer, default=0)
    hash = Column(String, unique=True, nullable=False)

    instances = relationship("ObjectInstance", back_populates="asset")
    properties = relationship("ObjectProperty", back_populates="asset")


class ObjectHierarchy(Base):
    __tablename__ = "object_hierarchy"
    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("object_store.id"), nullable=False)
    child_id = Column(Integer, ForeignKey("object_store.id"), nullable=False)
    __table_args__ = (UniqueConstraint("parent_id", "child_id"),)


class ObjectInstance(Base):
    __tablename__ = "object_instance"
    id = Column(Integer, primary_key=True)
    object_id = Column(Integer, ForeignKey("object_store.id"), nullable=False)
    path = Column(String, nullable=True)
    keys = Column(JSON, nullable=False, default={})
    parent_id = Column(Integer, ForeignKey("object_instance.id"), nullable=True)
    timestamp = Column(
        Integer, nullable=True
    )  # Unix mtime of the file/dir at registration

    asset = relationship("ObjectStore", back_populates="instances")
    data = relationship("ObjectData", back_populates="instance")
    __table_args__ = (UniqueConstraint("object_id", "path", "keys", "parent_id"),)


class ObjectProperty(Base):
    __tablename__ = "object_property"
    id = Column(Integer, primary_key=True)
    obj_id = Column(Integer, ForeignKey("object_store.id"), nullable=False)
    name = Column(String, nullable=False)
    hash = Column(String, nullable=False)
    serializer = Column(String, nullable=False, default="pickle")

    asset = relationship("ObjectStore", back_populates="properties")
    data = relationship("ObjectData", back_populates="property")
    __table_args__ = (UniqueConstraint("obj_id", "name"),)


class ObjectData(Base):
    __tablename__ = "object_data"
    id = Column(Integer, primary_key=True)
    obj_property_id = Column(Integer, ForeignKey("object_property.id"), nullable=False)
    obj_instance_id = Column(Integer, ForeignKey("object_instance.id"), nullable=False)
    property_hash = Column(String, nullable=False)
    data = Column(LargeBinary, nullable=True)

    property = relationship("ObjectProperty", back_populates="data")
    instance = relationship("ObjectInstance", back_populates="data")
    __table_args__ = (UniqueConstraint("obj_property_id", "obj_instance_id"),)


class ObjectBinding(Base):
    __tablename__ = "object_binding"
    id = Column(Integer, primary_key=True)
    signature = Column(String, unique=True)

    members = relationship("ObjectBindingMember", back_populates="binding")


class ObjectBindingMember(Base):
    __tablename__ = "object_binding_member"
    id = Column(Integer, primary_key=True)
    binding_id = Column(Integer, ForeignKey("object_binding.id"), nullable=False)
    object_store_id = Column(Integer, ForeignKey("object_store.id"), nullable=False)
    key_name = Column(String, nullable=False)

    binding = relationship("ObjectBinding", back_populates="members")


class ObjectPropertyDep(Base):
    __tablename__ = "object_property_dep"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("object_property.id"), nullable=False)
    depends_on_id = Column(Integer, ForeignKey("object_property.id"), nullable=False)
    __table_args__ = (UniqueConstraint("property_id", "depends_on_id"),)


class Database:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            path = str(Path(path).resolve())
        self.path = path
        db_url = f"sqlite:///{path}" if path != ":memory:" else "sqlite:///:memory:"
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        raw_conn = self.engine.connect()
        self.conn = SQLConnectionWrapper(raw_conn)
        Base.metadata.create_all(self.engine)
        self._group_session: Session | None = None
        self._group_depth = 0

    # ------------------------------------------------------------------
    # Pickle support (required for dask "processes" scheduler)
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict:
        if self.path == ":memory:":
            raise TypeError(
                "In-memory databases cannot be pickled for multiprocessing. "
                "Use a file-based database (i.e. pass path= to StaticAsset)."
            )
        return {"path": self.path}

    def __setstate__(self, state: dict) -> None:
        self.__init__(state["path"])

    def _session(self) -> Session:
        return Session(self.engine)

    def _acquire_session(self) -> tuple[Session, bool]:
        if self._group_session is not None:
            return self._group_session, False
        return self._session(), True

    def _release_session(self, session: Session, owns_session: bool) -> None:
        if owns_session:
            session.close()

    def _flush_or_commit(self, session: Session, owns_session: bool) -> None:
        if owns_session:
            session.commit()
        else:
            session.flush()

    @contextmanager
    def group_operations(self):
        """Group many DB operations into one transaction.

        Outside this context, each write method keeps the current behavior
        (commit per call). Inside this context, write methods flush changes
        and defer commit until exiting the outermost block.
        """
        if self._group_session is None:
            self._group_session = self._session()
        self._group_depth += 1
        try:
            yield self
            self._group_depth -= 1
            if self._group_depth == 0 and self._group_session is not None:
                self._group_session.commit()
                self._group_session.close()
                self._group_session = None
        except Exception:
            if self._group_session is not None:
                self._group_session.rollback()
                self._group_session.close()
                self._group_session = None
            self._group_depth = 0
            raise

    def register_asset_type(
        self, name: str, pattern: str | None, is_dynamic: bool, hash_: str
    ) -> int:

        session, owns_session = self._acquire_session()
        try:
            # Try to find existing
            existing = session.query(ObjectStore).filter_by(hash=hash_).first()
            if existing:
                return existing.id

            # Try to insert
            obj_store = ObjectStore(
                name=name, pattern=pattern, is_dynamic=int(is_dynamic), hash=hash_
            )
            session.add(obj_store)
            try:
                self._flush_or_commit(session, owns_session)
                return obj_store.id
            except IntegrityError:
                if not owns_session:
                    raise
                session.rollback()
                existing = session.query(ObjectStore).filter_by(hash=hash_).first()
                if existing:
                    return existing.id
                raise
        finally:
            self._release_session(session, owns_session)

    def register_hierarchy(self, parent_id: int, child_id: int):

        session, owns_session = self._acquire_session()
        try:
            existing = (
                session.query(ObjectHierarchy)
                .filter_by(parent_id=parent_id, child_id=child_id)
                .first()
            )
            if existing:
                return

            hierarchy = ObjectHierarchy(parent_id=parent_id, child_id=child_id)
            session.add(hierarchy)
            try:
                self._flush_or_commit(session, owns_session)
            except IntegrityError:
                if not owns_session:
                    raise
                session.rollback()
        finally:
            self._release_session(session, owns_session)

    def register_instance(
        self,
        object_id: int,
        path: str | None,
        keys: dict,
        parent_id: int | None,
        timestamp: int | None = None,
    ) -> int:
        """
        Register an instance. If path is a file/dir, timestamp should be its mtime (int, seconds since epoch).
        """
        session, owns_session = self._acquire_session()
        try:
            existing = (
                session.query(ObjectInstance)
                .filter_by(
                    object_id=object_id, path=path, keys=keys, parent_id=parent_id
                )
                .first()
            )
            if existing:
                # Optionally update timestamp if newer
                if timestamp is not None and (
                    existing.timestamp is None or existing.timestamp < timestamp
                ):
                    existing.timestamp = timestamp
                    self._flush_or_commit(session, owns_session)
                return existing.id

            instance = ObjectInstance(
                object_id=object_id,
                path=path,
                keys=keys,
                parent_id=parent_id,
                timestamp=timestamp,
            )
            session.add(instance)
            try:
                self._flush_or_commit(session, owns_session)
                return instance.id
            except IntegrityError:
                if not owns_session:
                    raise
                session.rollback()
                existing = (
                    session.query(ObjectInstance)
                    .filter_by(
                        object_id=object_id, path=path, keys=keys, parent_id=parent_id
                    )
                    .first()
                )
                if existing:
                    return existing.id
                raise
        finally:
            self._release_session(session, owns_session)

    def register_property(
        self, obj_id: int, name: str, source_hash: str, serializer: str = "pickle"
    ) -> int:

        session, owns_session = self._acquire_session()
        try:
            # Try to find existing (fresh query)
            existing = (
                session.query(ObjectProperty)
                .filter_by(obj_id=obj_id, name=name)
                .first()
            )
            if existing:
                existing.hash = source_hash
                self._flush_or_commit(session, owns_session)
                return existing.id

            # Try to insert; if duplicate, update instead
            prop = ObjectProperty(
                obj_id=obj_id, name=name, hash=source_hash, serializer=serializer
            )
            session.add(prop)
            try:
                self._flush_or_commit(session, owns_session)
                return prop.id
            except IntegrityError:
                if not owns_session:
                    raise
                # Record was inserted by another process; update it
                session.rollback()
                existing = (
                    session.query(ObjectProperty)
                    .filter_by(obj_id=obj_id, name=name)
                    .first()
                )
                if existing:
                    existing.hash = source_hash
                    self._flush_or_commit(session, owns_session)
                    return existing.id
                raise
        finally:
            self._release_session(session, owns_session)

    def get_cached_property(
        self, property_id: int, instance_id: int, current_hash: str
    ) -> bytes | None:
        session, owns_session = self._acquire_session()
        try:
            row = (
                session.query(ObjectData)
                .filter_by(obj_property_id=property_id, obj_instance_id=instance_id)
                .first()
            )
            if row is None or row.property_hash != current_hash:
                return None
            return row.data
        finally:
            self._release_session(session, owns_session)

    def set_cached_property(
        self, property_id: int, instance_id: int, data: bytes, hash_: str
    ):

        session, owns_session = self._acquire_session()
        try:
            existing = (
                session.query(ObjectData)
                .filter_by(obj_property_id=property_id, obj_instance_id=instance_id)
                .first()
            )
            if existing:
                existing.property_hash = hash_
                existing.data = data
                self._flush_or_commit(session, owns_session)
            else:
                obj_data = ObjectData(
                    obj_property_id=property_id,
                    obj_instance_id=instance_id,
                    property_hash=hash_,
                    data=data,
                )
                session.add(obj_data)
                try:
                    self._flush_or_commit(session, owns_session)
                except IntegrityError:
                    if not owns_session:
                        raise
                    session.rollback()
                    existing = (
                        session.query(ObjectData)
                        .filter_by(
                            obj_property_id=property_id, obj_instance_id=instance_id
                        )
                        .first()
                    )
                    if existing:
                        existing.property_hash = hash_
                        existing.data = data
                        self._flush_or_commit(session, owns_session)
        finally:
            self._release_session(session, owns_session)

    def register_property_dep(self, property_id: int, depends_on_id: int):

        session, owns_session = self._acquire_session()
        try:
            existing = (
                session.query(ObjectPropertyDep)
                .filter_by(property_id=property_id, depends_on_id=depends_on_id)
                .first()
            )
            if existing:
                return

            dep = ObjectPropertyDep(
                property_id=property_id, depends_on_id=depends_on_id
            )
            session.add(dep)
            try:
                self._flush_or_commit(session, owns_session)
            except IntegrityError:
                if not owns_session:
                    raise
                session.rollback()
        finally:
            self._release_session(session, owns_session)

    def get_property_dep_ids(self, property_id: int) -> list[int]:
        session, owns_session = self._acquire_session()
        try:
            rows = (
                session.query(ObjectPropertyDep)
                .filter_by(property_id=property_id)
                .all()
            )
            return [r.depends_on_id for r in rows]
        finally:
            self._release_session(session, owns_session)

    def get_child_instances(
        self, parent_instance_id: int, child_object_id: int
    ) -> list[dict]:
        session, owns_session = self._acquire_session()
        try:
            rows = (
                session.query(ObjectInstance)
                .filter_by(parent_id=parent_instance_id, object_id=child_object_id)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "object_id": r.object_id,
                    "path": r.path,
                    "keys": r.keys,
                    "parent_id": r.parent_id,
                }
                for r in rows
            ]
        finally:
            self._release_session(session, owns_session)

    def get_instances(self, object_id: int, key_filters: dict) -> list[dict]:
        session, owns_session = self._acquire_session()
        try:
            rows = session.query(ObjectInstance).filter_by(object_id=object_id).all()
            result = []
            for row in rows:
                if all(row.keys.get(k) == str(v) for k, v in key_filters.items()):
                    result.append(
                        {
                            "id": row.id,
                            "object_id": row.object_id,
                            "path": row.path,
                            "keys": row.keys,
                            "parent_id": row.parent_id,
                        }
                    )
            return result
        finally:
            self._release_session(session, owns_session)

    def get_bound_instances(
        self, instance_id: int, target_object_id: int
    ) -> list[dict]:
        session, owns_session = self._acquire_session()
        try:
            inst = session.query(ObjectInstance).filter_by(id=instance_id).first()
            if inst is None:
                return []

            source_object_id = inst.object_id
            source_keys = inst.keys

            bindings = (
                session.query(ObjectBinding)
                .join(
                    ObjectBindingMember,
                    ObjectBinding.id == ObjectBindingMember.binding_id,
                )
                .filter(ObjectBindingMember.object_store_id == source_object_id)
                .all()
            )

            result = []
            seen_ids: set[int] = set()

            for binding in bindings:
                # Get source keys for this binding
                source_members = [
                    m for m in binding.members if m.object_store_id == source_object_id
                ]
                for source_member in source_members:
                    source_key = source_member.key_name
                    key_value = source_keys.get(source_key)
                    if key_value is None:
                        continue

                    # Get target keys for this binding
                    target_members = [
                        m
                        for m in binding.members
                        if m.object_store_id == target_object_id
                    ]
                    for target_member in target_members:
                        target_key = target_member.key_name
                        target_instances = self.get_instances(
                            target_object_id, {target_key: key_value}
                        )
                        for ti in target_instances:
                            if ti["id"] not in seen_ids:
                                seen_ids.add(ti["id"])
                                result.append(ti)

            return result
        finally:
            self._release_session(session, owns_session)

    def register_binding(self, members: list[tuple]) -> int:
        """Register a binding. members is [(object_store_id, key_name), ...]."""

        sig = hashlib.sha256(
            json.dumps(sorted(members), sort_keys=True).encode()
        ).hexdigest()

        session, owns_session = self._acquire_session()
        try:
            existing = session.query(ObjectBinding).filter_by(signature=sig).first()
            if existing:
                return existing.id

            binding = ObjectBinding(signature=sig)
            session.add(binding)
            session.flush()

            for obj_id, key_name in members:
                member = ObjectBindingMember(
                    binding_id=binding.id,
                    object_store_id=obj_id,
                    key_name=key_name,
                )
                session.add(member)

            try:
                self._flush_or_commit(session, owns_session)
                return binding.id
            except IntegrityError:
                if not owns_session:
                    raise
                session.rollback()
                existing = session.query(ObjectBinding).filter_by(signature=sig).first()
                if existing:
                    return existing.id
                raise
        finally:
            self._release_session(session, owns_session)
