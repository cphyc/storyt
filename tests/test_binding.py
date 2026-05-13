"""Tests for bind() and instance.bound()."""
import pytest
import storyt as st


@pytest.fixture
def make_root(tmp_path):
    def _factory(outputs=(1, 2, 3), cutouts=(1, 2, 3)):
        for i in outputs:
            (tmp_path / f"output_{i:05d}").mkdir()
        for i in cutouts:
            (tmp_path / f"cutout_{i:05d}").mkdir()

        root = st.StaticAsset(path=str(tmp_path), name="root")
        output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
        cutout = root.add_children(re=r"cutout_(?P<iout>\d{5})", name="cutout")
        return root, output, cutout
    return _factory


def test_bind_creates_db_records(make_root):
    root, output, cutout = make_root()
    st.bind((output, "iout"), (cutout, "iout"))
    root.discover()

    db = root._db
    bindings = db.conn.execute("SELECT * FROM object_binding").fetchall()
    assert len(bindings) == 1

    members = db.conn.execute("SELECT * FROM object_binding_member").fetchall()
    assert len(members) == 2


def test_bind_idempotent_across_discovers(make_root):
    """Calling discover() twice must not duplicate binding records."""
    root, output, cutout = make_root()
    st.bind((output, "iout"), (cutout, "iout"))
    root.discover()
    root.discover()

    db = root._db
    bindings = db.conn.execute("SELECT * FROM object_binding").fetchall()
    assert len(bindings) == 1

    members = db.conn.execute("SELECT * FROM object_binding_member").fetchall()
    assert len(members) == 2


def test_bound_returns_matching_instance(make_root):
    root, output, cutout = make_root(outputs=[1, 2], cutouts=[1, 2])
    st.bind((output, "iout"), (cutout, "iout"))
    root.discover()

    out_inst = output.instances(iout="00001")[0]
    bound_cutouts = out_inst.bound("cutout")

    assert len(bound_cutouts) == 1
    assert bound_cutouts[0].keys["iout"] == "00001"


def test_bound_returns_empty_when_no_match(make_root):
    """An output with iout=00001 has no cutout partner with iout=00001."""
    root, output, cutout = make_root(outputs=[1], cutouts=[2])
    st.bind((output, "iout"), (cutout, "iout"))
    root.discover()

    out_inst = output.instances(iout="00001")[0]
    bound_cutouts = out_inst.bound("cutout")
    assert len(bound_cutouts) == 0


def test_three_way_bind(tmp_path):
    for i in [1, 2]:
        (tmp_path / f"output_{i:05d}").mkdir()
        (tmp_path / f"cutout_{i:05d}").mkdir()
        (tmp_path / f"halo_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    cutout = root.add_children(re=r"cutout_(?P<iout>\d{5})", name="cutout")
    halo = root.add_children(re=r"halo_(?P<iout>\d{5})", name="halo")

    st.bind((output, "iout"), (cutout, "iout"), (halo, "iout"))
    root.discover()

    out_inst = output.instances(iout="00001")[0]

    bound_cutouts = out_inst.bound("cutout")
    assert len(bound_cutouts) == 1
    assert bound_cutouts[0].keys["iout"] == "00001"

    bound_halos = out_inst.bound("halo")
    assert len(bound_halos) == 1
    assert bound_halos[0].keys["iout"] == "00001"


def test_bound_unknown_name(make_root):
    root, output, cutout = make_root(outputs=[1], cutouts=[1])
    st.bind((output, "iout"), (cutout, "iout"))
    root.discover()

    out_inst = output.instances(iout="00001")[0]
    assert out_inst.bound("does_not_exist") == []


def test_multiple_bindings(tmp_path):
    """Two separate bindings on different key names both work."""
    for i in [1, 2]:
        (tmp_path / f"a_{i:05d}").mkdir()
        (tmp_path / f"b_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    a = root.add_children(re=r"a_(?P<iout>\d{5})", name="a")
    b = root.add_children(re=r"b_(?P<iout>\d{5})", name="b")

    st.bind((a, "iout"), (b, "iout"))
    root.discover()

    a_inst = a.instances(iout="00001")[0]
    bound_b = a_inst.bound("b")
    assert len(bound_b) == 1
    assert bound_b[0].keys["iout"] == "00001"
