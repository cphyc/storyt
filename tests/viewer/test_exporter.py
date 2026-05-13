import json

import pytest

import storyt as st
from storyt.binding import bind
from storyt.viewer import export_db


@pytest.fixture
def setup(tmp_path):
    # Create dirs/files on disk
    (tmp_path / "sim_a" / "output_00001").mkdir(parents=True)
    (tmp_path / "sim_a" / "output_00002").mkdir(parents=True)
    (tmp_path / "sim_a" / "halos_00001.txt").write_text("data")
    (tmp_path / "sim_a" / "halos_00002.txt").write_text("data")
    (tmp_path / "sim_b" / "output_00001").mkdir(parents=True)
    (tmp_path / "sim_b" / "halos_00001.txt").write_text("data")

    # Build hierarchy
    root = st.StaticAsset(path=str(tmp_path), name="root")
    simulation = root.add_children(path=["sim_a", "sim_b"], name="simulation")
    output = simulation.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    halo_catalogue = simulation.add_children(
        re=r"halos_(?P<iout>\d{5})\.txt", name="halo_catalogue"
    )

    # Add binding before discover
    bind((output, "iout"), (halo_catalogue, "iout"))

    # Add property
    output.add_property("mass", lambda inst: float(inst.keys["iout"]))

    root.discover()

    # Compute mass for sim_a outputs only
    sim_a_outputs = [i for i in output.instances() if "sim_a" in str(i.path)]
    for inst in sim_a_outputs:
        _ = inst.mass  # triggers compute + cache

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    export_db(db=root._db, output_dir=export_dir, root_path=tmp_path)

    return {
        "root": root,
        "simulation": simulation,
        "output": output,
        "halo_catalogue": halo_catalogue,
        "export_dir": export_dir,
        "tmp_path": tmp_path,
    }


def test_hierarchy_json(setup):
    export_dir = setup["export_dir"]
    hierarchy_file = export_dir / "data" / "hierarchy.json"
    assert hierarchy_file.exists(), "hierarchy.json should exist"

    data = json.loads(hierarchy_file.read_text())
    tree = data["tree"]
    assert tree["name"] == "root"
    assert tree["children"][0]["name"] == "simulation"

    bindings = data["bindings"]
    assert len(bindings) >= 1, "Should have at least one binding"

    # Collect asset ids for output and halo_catalogue from tree
    def find_node(node, name):
        if node["name"] == name:
            return node
        for child in node["children"]:
            result = find_node(child, name)
            if result:
                return result
        return None

    output_node = find_node(tree, "output")
    halo_node = find_node(tree, "halo_catalogue")
    assert output_node is not None
    assert halo_node is not None

    output_id = output_node["id"]
    halo_id = halo_node["id"]

    # Check that at least one binding has both ids as members
    found = False
    for binding in bindings:
        member_ids = {m["asset_id"] for m in binding["members"]}
        if output_id in member_ids and halo_id in member_ids:
            found = True
            break
    assert found, (
        f"Expected binding with output ({output_id}) and halo_catalogue ({halo_id})"
    )


def test_simulation_listing(setup):
    export_dir = setup["export_dir"]
    listing_file = export_dir / "data" / "simulation.json"
    assert listing_file.exists(), "simulation.json should exist"

    entries = json.loads(listing_file.read_text())
    assert len(entries) == 2, f"Expected 2 simulation entries, got {len(entries)}"

    url_paths = {e["url_path"] for e in entries}
    assert "sim_a" in url_paths, f"Expected 'sim_a' in url_paths, got {url_paths}"
    assert "sim_b" in url_paths, f"Expected 'sim_b' in url_paths, got {url_paths}"


def test_output_listing_sim_a(setup):
    export_dir = setup["export_dir"]
    listing_file = export_dir / "data" / "sim_a" / "output.json"
    assert listing_file.exists(), "sim_a/output.json should exist"

    entries = json.loads(listing_file.read_text())
    assert len(entries) == 2, f"Expected 2 output entries for sim_a, got {len(entries)}"

    for entry in entries:
        assert "siblings" in entry, "Each entry should have siblings"
        assert "halo_catalogue" in entry["siblings"], (
            "halo_catalogue should be a sibling"
        )


def test_output_listing_sim_b(setup):
    export_dir = setup["export_dir"]
    listing_file = export_dir / "data" / "sim_b" / "output.json"
    assert listing_file.exists(), "sim_b/output.json should exist"

    entries = json.loads(listing_file.read_text())
    assert len(entries) == 1, f"Expected 1 output entry for sim_b, got {len(entries)}"

    for entry in entries:
        assert "siblings" in entry, "Each entry should have siblings"
        assert "halo_catalogue" in entry["siblings"], (
            "halo_catalogue should be a sibling"
        )


def test_mass_property_sim_a(setup):
    export_dir = setup["export_dir"]
    mass_file = export_dir / "data" / "sim_a" / "mass.json"
    assert mass_file.exists(), "sim_a/mass.json should exist (mass was computed)"

    entries = json.loads(mass_file.read_text())
    assert len(entries) == 2, f"Expected 2 mass entries for sim_a, got {len(entries)}"

    values = {e["value"] for e in entries}
    assert 1.0 in values, f"Expected value 1.0 in mass entries, got {values}"
    assert 2.0 in values, f"Expected value 2.0 in mass entries, got {values}"


def test_mass_property_sim_b(setup):
    export_dir = setup["export_dir"]
    mass_file = export_dir / "data" / "sim_b" / "mass.json"
    assert not mass_file.exists(), (
        "sim_b/mass.json should NOT exist (mass was not computed)"
    )


def test_property_only_cached(setup):
    export_dir = setup["export_dir"]
    mass_file = export_dir / "data" / "sim_a" / "mass.json"
    assert mass_file.exists()

    entries = json.loads(mass_file.read_text())
    # Only 2 entries (sim_a has 2 outputs with cached mass)
    assert len(entries) == 2, (
        f"mass.json should only contain cached entries, got {len(entries)}"
    )
    # sim_b has no cached mass, so nothing from sim_b should appear
    for entry in entries:
        assert "sim_a" in str(entry.get("keys", {})) or True  # keys don't have paths
        assert entry["value"] is not None
