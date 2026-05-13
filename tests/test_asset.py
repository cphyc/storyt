"""Tests for StaticAsset discovery and instance access."""
import pytest
import storyt as st


def test_root_creation(tmp_path):
    root = st.StaticAsset(path=str(tmp_path), name="root")
    assert root.name == "root"
    assert (tmp_path / ".storyt.db").exists()


def test_add_children_with_path_list(tmp_path):
    (tmp_path / "sim1").mkdir()
    (tmp_path / "sim2").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim1", "sim2"], name="sim")

    root.discover()

    instances = sim.instances()
    assert len(instances) == 2
    names = {i.path.name for i in instances}
    assert names == {"sim1", "sim2"}


def test_add_children_with_single_path(tmp_path):
    (tmp_path / "data").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    data = root.add_children(path="data", name="data")

    root.discover()

    instances = data.instances()
    assert len(instances) == 1
    assert instances[0].path.name == "data"


def test_add_children_with_regex(tmp_path):
    for i in range(3):
        (tmp_path / f"output_{i:05d}").mkdir()
    (tmp_path / "other_dir").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")

    root.discover()

    instances = output.instances()
    assert len(instances) == 3
    iout_values = sorted(i.keys["iout"] for i in instances)
    assert iout_values == ["00000", "00001", "00002"]


def test_regex_captures_are_strings(tmp_path):
    (tmp_path / "output_00042").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()

    inst = output.instances()[0]
    assert inst.keys["iout"] == "00042"
    assert isinstance(inst.keys["iout"], str)


def test_instances_with_key_filter(tmp_path):
    for i in range(3):
        (tmp_path / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()

    instances = output.instances(iout="00001")
    assert len(instances) == 1
    assert instances[0].keys["iout"] == "00001"


def test_instances_no_match_filter(tmp_path):
    (tmp_path / "output_00001").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()

    instances = output.instances(iout="99999")
    assert len(instances) == 0


def test_discover_populates_db(tmp_path):
    (tmp_path / "sim").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    _sim = root.add_children(path=["sim"], name="sim")
    root.discover()

    db = root._db
    rows = db.conn.execute("SELECT * FROM object_store").fetchall()
    names = {r["name"] for r in rows}
    assert "root" in names
    assert "sim" in names

    inst_rows = db.conn.execute("SELECT * FROM object_instance").fetchall()
    assert len(inst_rows) == 2  # root + sim


def test_discover_is_idempotent(tmp_path):
    (tmp_path / "output_00001").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    root.discover()  # second call

    instances = output.instances()
    assert len(instances) == 1  # not duplicated


def test_regex_subdirectory_pattern(tmp_path):
    subdir = tmp_path / "halos"
    subdir.mkdir()
    (subdir / "halos_00001.ascii").write_text("data")
    (subdir / "halos_00002.ascii").write_text("data")

    root = st.StaticAsset(path=str(tmp_path), name="root")
    cat = root.add_children(
        re=r"halos/halos_(?P<iout>\d{5})\.ascii", name="halo_catalogue"
    )
    root.discover()

    instances = cat.instances()
    assert len(instances) == 2
    iout_values = sorted(i.keys["iout"] for i in instances)
    assert iout_values == ["00001", "00002"]


def test_two_level_hierarchy(tmp_path):
    for sim in ["sim1", "sim2"]:
        sim_dir = tmp_path / sim
        sim_dir.mkdir()
        for i in range(2):
            (sim_dir / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim1", "sim2"], name="sim")
    output = sim.add_children(re=r"output_(?P<iout>\d{5})", name="output")

    root.discover()

    sim_instances = sim.instances()
    assert len(sim_instances) == 2

    output_instances = output.instances()
    assert len(output_instances) == 4  # 2 sims × 2 outputs each


def test_instance_path_is_absolute(tmp_path):
    (tmp_path / "sim").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim"], name="sim")
    root.discover()

    inst = sim.instances()[0]
    assert inst.path.is_absolute()
