from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from storyt.db import (
    ObjectBinding,
    ObjectBindingMember,
    ObjectData,
    ObjectHierarchy,
    ObjectInstance,
    ObjectProperty,
    ObjectStore,
)
from storyt.serializers import deserialize

if TYPE_CHECKING:
    from storyt.db import Database


def _build_asset_tree(session) -> dict:
    """Build asset type tree in memory. Returns root node dict."""
    all_stores = session.query(ObjectStore).all()
    all_hierarchies = session.query(ObjectHierarchy).all()

    child_ids = {h.child_id for h in all_hierarchies}
    parent_map: dict[int, list[int]] = {}
    for h in all_hierarchies:
        parent_map.setdefault(h.parent_id, []).append(h.child_id)

    store_by_id = {s.id: s for s in all_stores}

    # Root: not in any child_id
    root_stores = [s for s in all_stores if s.id not in child_ids]
    if not root_stores:
        raise ValueError("No root asset type found")

    def build_node(store_id: int) -> dict:
        s = store_by_id[store_id]
        children = [build_node(cid) for cid in parent_map.get(store_id, [])]
        return {
            "id": s.id,
            "name": s.name,
            "pattern": s.pattern,
            "is_dynamic": bool(s.is_dynamic),
            "children": children,
        }

    return build_node(root_stores[0].id)


def _get_bindings(session) -> list[dict]:
    """Get all bindings with their members."""
    bindings = session.query(ObjectBinding).all()
    result = []
    for b in bindings:
        members = [
            {"asset_id": m.object_store_id, "key_name": m.key_name} for m in b.members
        ]
        result.append({"id": b.id, "members": members})
    return result


def _instance_url_path(inst_row, root_path: Path) -> str | None:
    """Compute url_path for an instance row (dict with 'path' key)."""
    if inst_row["path"] is None:
        return None
    try:
        return Path(inst_row["path"]).relative_to(root_path).as_posix()
    except ValueError:
        return None


def _walk_up_for_url_path(
    instance_id: int,
    instance_by_id: dict[int, dict],
    root_path: Path,
) -> str | None:
    """Walk up the instance tree to find the nearest ancestor with a non-null path."""
    inst = instance_by_id.get(instance_id)
    if inst is None:
        return None
    if inst["path"] is not None:
        return _instance_url_path(inst, root_path)
    if inst["parent_id"] is None:
        return None
    return _walk_up_for_url_path(inst["parent_id"], instance_by_id, root_path)


def export_db(db: Database, output_dir: Path | str, root_path: Path | str) -> None:
    """Export the full database to a static file tree under output_dir."""
    output_dir = Path(output_dir)
    root_path = Path(root_path)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    session = db._session()
    try:
        # ------------------------------------------------------------------ #
        # Build asset-type tree + bindings -> hierarchy.json                  #
        # ------------------------------------------------------------------ #
        tree = _build_asset_tree(session)
        bindings = _get_bindings(session)

        hierarchy_path = data_dir / "hierarchy.json"
        hierarchy_path.write_text(
            json.dumps({"tree": tree, "bindings": bindings}, default=str)
        )

        # ------------------------------------------------------------------ #
        # Load all instances into memory                                        #
        # ------------------------------------------------------------------ #
        all_instances = session.query(ObjectInstance).all()
        instance_by_id: dict[int, dict] = {}
        for inst in all_instances:
            keys = inst.keys if isinstance(inst.keys, dict) else json.loads(inst.keys)
            instance_by_id[inst.id] = {
                "id": inst.id,
                "object_id": inst.object_id,
                "path": inst.path,
                "keys": keys,
                "parent_id": inst.parent_id,
            }

        # ------------------------------------------------------------------ #
        # Load all properties and cached data                                  #
        # ------------------------------------------------------------------ #
        all_properties = session.query(ObjectProperty).all()
        # prop_by_id: maps property id -> ObjectProperty
        prop_by_id = {p.id: p for p in all_properties}
        # props_by_obj_id: maps object_store_id -> list of ObjectProperty
        props_by_obj_id: dict[int, list] = {}
        for p in all_properties:
            props_by_obj_id.setdefault(p.obj_id, []).append(p)

        all_data = session.query(ObjectData).all()
        # cached_data: (property_id, instance_id) -> ObjectData (only valid cache)
        cached_data: dict[tuple[int, int], ObjectData] = {}
        for d in all_data:
            prop = prop_by_id.get(d.obj_property_id)
            if prop and d.property_hash == prop.hash:
                cached_data[d.obj_property_id, d.obj_instance_id] = d

        # ------------------------------------------------------------------ #
        # Identify root instance (parent_id = NULL)                            #
        # ------------------------------------------------------------------ #
        root_instances = [v for v in instance_by_id.values() if v["parent_id"] is None]
        if not root_instances:
            return
        root_inst = root_instances[0]

        # ------------------------------------------------------------------ #
        # Load bindings for sibling resolution                                 #
        # ------------------------------------------------------------------ #
        # For each object_store_id, which bindings include it?
        # binding_members_by_obj: obj_store_id -> list of (binding_id, key_name)
        all_binding_members = session.query(ObjectBindingMember).all()
        obj_to_bindings: dict[int, list] = {}
        for m in all_binding_members:
            obj_to_bindings.setdefault(m.object_store_id, []).append(m)
        # binding_id -> list of ObjectBindingMember
        members_by_binding: dict[int, list] = {}
        for m in all_binding_members:
            members_by_binding.setdefault(m.binding_id, []).append(m)

        # ------------------------------------------------------------------ #
        # Build flat asset list from tree (DFS)                                #
        # ------------------------------------------------------------------ #
        def iter_tree_nodes(node: dict):
            yield node
            for child in node["children"]:
                yield from iter_tree_nodes(child)

        asset_nodes = list(iter_tree_nodes(tree))
        root_asset_id = tree["id"]

        # instances grouped by object_id
        insts_by_obj: dict[int, list[dict]] = {}
        for inst in instance_by_id.values():
            insts_by_obj.setdefault(inst["object_id"], []).append(inst)

        # ------------------------------------------------------------------ #
        # Helper: get sibling info for an instance                             #
        # ------------------------------------------------------------------ #
        def get_siblings(inst: dict) -> dict:
            obj_id = inst["object_id"]
            siblings: dict[str, dict] = {}
            for member in obj_to_bindings.get(obj_id, []):
                binding_id = member.binding_id
                cur_key_name = member.key_name
                cur_key_value = inst["keys"].get(cur_key_name)
                if cur_key_value is None:
                    continue
                for other_member in members_by_binding.get(binding_id, []):
                    if other_member.object_store_id == obj_id:
                        continue
                    sibling_obj_id = other_member.object_store_id
                    sibling_key_name = other_member.key_name
                    # Find sibling instance matching key value
                    sibling_inst = None
                    for si in insts_by_obj.get(sibling_obj_id, []):
                        if si["keys"].get(sibling_key_name) == cur_key_value:
                            sibling_inst = si
                            break
                    # Get sibling asset store name
                    sibling_store = (
                        session.query(ObjectStore).filter_by(id=sibling_obj_id).first()
                    )
                    if sibling_store is None:
                        continue
                    sibling_name = sibling_store.name
                    # Get property names for sibling
                    prop_names = [
                        p.name for p in props_by_obj_id.get(sibling_obj_id, [])
                    ]
                    if sibling_inst:
                        sib_url = _instance_url_path(sibling_inst, root_path)
                        siblings[sibling_name] = {
                            "id": sibling_inst["id"],
                            "keys": sibling_inst["keys"],
                            "path": sibling_inst["path"],
                            "url_path": sib_url,
                            "properties": prop_names,
                        }
                    else:
                        siblings[sibling_name] = {
                            "id": None,
                            "keys": {},
                            "path": None,
                            "url_path": None,
                            "properties": prop_names,
                        }
            return siblings

        # ------------------------------------------------------------------ #
        # Write listing files                                                  #
        # ------------------------------------------------------------------ #
        def write_listing(asset_node: dict, parent_inst: dict):
            """Write the listing JSON for children of a given parent instance."""
            obj_id = asset_node["id"]
            name = asset_node["name"]
            children_of_parent = [
                i
                for i in insts_by_obj.get(obj_id, [])
                if i["parent_id"] == parent_inst["id"]
            ]
            if not children_of_parent:
                return

            # Compute url_path for parent
            parent_url = _instance_url_path(parent_inst, root_path)

            # Build entries
            entries = []
            for child_inst in children_of_parent:
                child_url = _instance_url_path(child_inst, root_path)
                entry: dict = {
                    "id": child_inst["id"],
                    "keys": child_inst["keys"],
                    "path": child_inst["path"],
                    "url_path": child_url,
                }
                # Add siblings for non-root level
                if parent_inst["parent_id"] is not None:
                    sibs = get_siblings(child_inst)
                    if sibs:
                        entry["siblings"] = sibs
                else:
                    # Direct children of root (one level down)
                    sibs = get_siblings(child_inst)
                    if sibs:
                        entry["siblings"] = sibs
                entries.append(entry)

            # Determine output path
            if parent_inst["id"] == root_inst["id"]:
                # Direct children of root -> data/<asset_type_name>.json
                out_file = data_dir / f"{name}.json"
            else:
                if parent_url is None:
                    # Dynamic parent: walk up
                    ancestor_url = _walk_up_for_url_path(
                        parent_inst["id"], instance_by_id, root_path
                    )
                    if ancestor_url is None:
                        return
                    out_file = data_dir / ancestor_url / f"{name}.json"
                else:
                    out_file = data_dir / parent_url / f"{name}.json"

            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(entries, default=str))

        # Write listings for each asset type (skip root)
        for asset_node in asset_nodes:
            if asset_node["id"] == root_asset_id:
                continue
            # Get parent asset id
            parent_asset_ids = [
                h.parent_id
                for h in session.query(ObjectHierarchy)
                .filter_by(child_id=asset_node["id"])
                .all()
            ]
            for parent_asset_id in parent_asset_ids:
                parent_instances = insts_by_obj.get(parent_asset_id, [])
                for parent_inst in parent_instances:
                    write_listing(asset_node, parent_inst)

        # ------------------------------------------------------------------ #
        # Write property files                                                 #
        # ------------------------------------------------------------------ #
        # For each property, group instances by their parent's url_path
        for prop in all_properties:
            # instances of this property's asset type
            asset_instances = insts_by_obj.get(prop.obj_id, [])

            # Group by parent url_path
            # parent_url_path -> list of (instance, cached_data_row)
            grouped: dict[str, list[dict]] = {}
            for inst in asset_instances:
                data_row = cached_data.get((prop.id, inst["id"]))
                if data_row is None:
                    continue
                # Determine parent url_path
                if inst["parent_id"] is None:
                    parent_url = ""
                else:
                    parent_inst = instance_by_id.get(inst["parent_id"])
                    if parent_inst is None:
                        continue
                    parent_url = _walk_up_for_url_path(
                        parent_inst["id"], instance_by_id, root_path
                    )
                    if parent_url is None:
                        continue

                # Deserialize value
                try:
                    value = deserialize(prop.serializer, data_row.data)
                    # Check JSON serializable
                    json.dumps(value)
                except Exception:
                    try:
                        value = str(deserialize(prop.serializer, data_row.data))
                    except Exception:
                        value = None

                grouped.setdefault(parent_url, []).append(
                    {"id": inst["id"], "keys": inst["keys"], "value": value}
                )

            for parent_url, entries in grouped.items():
                if parent_url:
                    out_file = data_dir / parent_url / f"{prop.name}.json"
                else:
                    out_file = data_dir / f"{prop.name}.json"
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(json.dumps(entries, default=str))

    finally:
        session.close()

    # ------------------------------------------------------------------ #
    # Copy built frontend dist/ if it exists; warn otherwise               #
    # ------------------------------------------------------------------ #
    frontend_dist = Path(__file__).parent / "frontend" / "dist"
    if frontend_dist.exists():
        for item in frontend_dist.iterdir():
            dest = output_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    else:
        import warnings

        warnings.warn(
            "storyt viewer frontend not built. "
            "Run `npm run build` in storyt/viewer/frontend/ first.",
            stacklevel=2,
        )
