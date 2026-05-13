"""Tests for StaticAsset discovery and instance access."""

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


# ---------------------------------------------------------------------------
# Tests for ancestor-path template substitution in re= patterns
# ---------------------------------------------------------------------------


def test_template_absolute_base(tmp_path):
    """${name.path} resolves the ancestor's path as the scan root."""
    cutout_base = tmp_path / "cutouts"
    cutout_base.mkdir()
    (cutout_base / "output_00001").mkdir()
    (cutout_base / "output_00002").mkdir()
    (cutout_base / "other").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    cutout = root.add_children(
        re=r"${root.path}/cutouts/output_(?P<iout>\d{5})",
        name="cutout",
    )

    root.discover()

    instances = cutout.instances()
    assert len(instances) == 2
    iout_values = sorted(i.keys["iout"] for i in instances)
    assert iout_values == ["00001", "00002"]


def test_template_path_name_attr(tmp_path):
    """${name.path.name} resolves to the last path component of an ancestor."""
    # Structure: tmp_path/sims/simA/  and  tmp_path/cutouts/simA/output_00001/
    (tmp_path / "sims" / "simA").mkdir(parents=True)
    cutout_dir = tmp_path / "cutouts" / "simA"
    cutout_dir.mkdir(parents=True)
    (cutout_dir / "output_00001").mkdir()
    (cutout_dir / "output_00042").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(re=r"sims/(?P<sim_name>\w+)", name="simulation")
    cutout = sim.add_children(
        re=r"${root.path}/cutouts/${simulation.path.name}/output_(?P<iout>\d{5})",
        name="cutout",
    )

    root.discover()

    sim_instances = sim.instances()
    assert len(sim_instances) == 1

    cutout_instances = cutout.instances()
    assert len(cutout_instances) == 2
    iout_values = sorted(i.keys["iout"] for i in cutout_instances)
    assert iout_values == ["00001", "00042"]


def test_template_multiple_sims(tmp_path):
    """Template resolves independently per simulation instance."""
    for sim_name in ["simA", "simB"]:
        (tmp_path / "sims" / sim_name).mkdir(parents=True)
        cutout_dir = tmp_path / "cutouts" / sim_name
        cutout_dir.mkdir(parents=True)
        for i in range(2):
            (cutout_dir / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(re=r"sims/(?P<sim_name>\w+)", name="simulation")
    cutout = sim.add_children(
        re=r"${root.path}/cutouts/${simulation.path.name}/output_(?P<iout>\d{5})",
        name="cutout",
    )

    root.discover()

    sim_instances = sim.instances()
    assert len(sim_instances) == 2

    cutout_instances = cutout.instances()
    # 2 sims × 2 outputs each = 4 total
    assert len(cutout_instances) == 4


def test_template_missing_ancestor_is_silently_skipped(tmp_path):
    """If a template references an unknown ancestor, the child is skipped gracefully."""
    (tmp_path / "sims" / "simA").mkdir(parents=True)

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(re=r"sims/(?P<sim_name>\w+)", name="simulation")
    # Misspelled ancestor name: "typo" doesn't exist
    bad = sim.add_children(
        re=r"${typo.path}/output_(?P<iout>\d{5})",
        name="bad",
    )

    root.discover()  # should not raise

    assert bad.instances() == []
