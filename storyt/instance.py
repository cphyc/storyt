from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .asset import StaticAsset


class AssetInstance:
    def __init__(
        self,
        asset: "StaticAsset",
        db_id: int,
        path: Optional[Path],
        keys: dict,
        parent_id: Optional[int],
    ):
        self.asset = asset
        self.db_id = db_id
        self.path = path
        self.keys = keys
        self.parent_id = parent_id

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load(self):
        if self.asset._reader is None:
            raise RuntimeError(
                f"No reader defined for asset '{self.asset.name}'"
            )
        return self.asset._reader(self.path)

    # ------------------------------------------------------------------
    # Binding resolution
    # ------------------------------------------------------------------

    def bound(self, name: str) -> list["AssetInstance"]:
        """Return instances of the named asset type bound to this instance."""
        target_asset = self._find_asset_by_name(name)
        if target_asset is None or target_asset._db_id is None:
            return []

        rows = self.asset._db.get_bound_instances(self.db_id, target_asset._db_id)
        result = []
        for row in rows:
            keys = (
                json.loads(row["keys"])
                if isinstance(row["keys"], str)
                else row["keys"]
            )
            path = Path(row["path"]) if row["path"] else None
            result.append(
                AssetInstance(target_asset, row["id"], path, keys, row.get("parent_id"))
            )
        return result

    def _find_asset_by_name(self, name: str) -> Optional["StaticAsset"]:
        # Walk up to root, then search the whole tree
        root = self.asset
        while root._parent is not None:
            root = root._parent
        return _search_asset(root, name)

    # ------------------------------------------------------------------
    # Property access with caching
    # ------------------------------------------------------------------

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        # Safely retrieve internal attributes without recursion
        try:
            asset = object.__getattribute__(self, "asset")
            properties = asset._properties
        except AttributeError:
            raise AttributeError(name)

        if name not in properties:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )

        prop = properties[name]
        source_hash = prop.source_hash()

        db = asset._db
        asset_db_id = asset._db_id
        inst_id = object.__getattribute__(self, "db_id")

        if asset_db_id is None:
            raise RuntimeError(f"Asset '{asset.name}' is not registered in the DB")

        prop_id = db.register_property(asset_db_id, name, source_hash, prop.serializer)

        cached = db.get_cached_property(prop_id, inst_id, source_hash)
        if cached is not None:
            from . import serializers
            return serializers.deserialize(prop.serializer, cached)

        # Resolve dependencies first (recursive, each dep is also cached)
        dep_values = tuple(getattr(self, dep) for dep in prop.requires)

        value = prop.compute(self, dep_values)

        from . import serializers
        data = serializers.serialize(prop.serializer, value)
        db.set_cached_property(prop_id, inst_id, data, source_hash)

        return value

    def __repr__(self) -> str:
        return (
            f"AssetInstance(name={self.asset.name!r}, "
            f"path={self.path}, keys={self.keys})"
        )


def _search_asset(asset: "StaticAsset", name: str) -> Optional["StaticAsset"]:
    if asset.name == name:
        return asset
    for child in asset._children:
        result = _search_asset(child, name)
        if result is not None:
            return result
    return None
