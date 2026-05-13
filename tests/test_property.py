"""Tests for property registration, computation, and caching."""

import pytest

import storyt as st
from storyt.property_ import Property


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "sim").mkdir()
    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim"], name="sim")
    root.discover()
    return root, sim


def test_add_property_registers(setup):
    root, sim = setup
    sim.add_property("answer", lambda inst: 42)
    assert "answer" in sim._properties


def test_add_property_decorator(setup):
    root, sim = setup

    @sim.add_property("my_val")
    def _compute(inst):
        return 99

    assert "my_val" in sim._properties


def test_property_access_computes_value(setup):
    root, sim = setup
    sim.add_property("answer", lambda inst: 42)

    inst = sim.instances()[0]
    assert inst.answer == 42


def test_property_caching(setup):
    root, sim = setup

    call_count = [0]

    def my_prop(inst):
        call_count[0] += 1
        return 42

    sim.add_property("my_prop", my_prop)
    inst = sim.instances()[0]

    val1 = inst.my_prop
    val2 = inst.my_prop

    assert val1 == val2 == 42
    assert call_count[0] == 1  # computed only once


def test_property_cache_invalidation_on_source_change(setup):
    """When the source hash changes, the property is recomputed."""
    root, sim = setup

    call_count = [0]

    def my_prop_v1(inst):
        call_count[0] += 1
        return 42

    sim.add_property("my_prop", my_prop_v1)
    inst = sim.instances()[0]
    val1 = inst.my_prop
    assert val1 == 42
    assert call_count[0] == 1

    # Replace with a different function (different source → different hash)
    def my_prop_v2(inst):
        call_count[0] += 1
        return 99  # different return value

    sim.add_property("my_prop", my_prop_v2)

    # Access again via fresh instance lookup
    inst2 = sim.instances()[0]
    val2 = inst2.my_prop
    assert val2 == 99
    assert call_count[0] == 2  # had to recompute


def test_property_unknown_attribute(setup):
    root, sim = setup
    inst = sim.instances()[0]
    with pytest.raises(AttributeError):
        _ = inst.nonexistent_property


def test_property_stored_in_db(setup):
    root, sim = setup
    sim.add_property("answer", lambda inst: 42)

    inst = sim.instances()[0]
    _ = inst.answer  # trigger computation + caching

    db = root._db
    rows = db.conn.execute("SELECT * FROM object_property").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "answer"

    data_rows = db.conn.execute("SELECT * FROM object_data").fetchall()
    assert len(data_rows) == 1


def test_property_source_hash_changes_with_different_fn(tmp_path):
    def fn_a(inst):
        return 1

    def fn_b(inst):
        return 2  # different body

    prop_a = Property("p", fn_a)
    prop_b = Property("p", fn_b)
    assert prop_a.source_hash() != prop_b.source_hash()


def test_property_complex_value(setup):
    root, sim = setup
    sim.add_property("data", lambda inst: {"a": [1, 2, 3], "b": "hello"})

    inst = sim.instances()[0]
    val = inst.data
    assert val == {"a": [1, 2, 3], "b": "hello"}
    # Second access from cache
    val2 = inst.data
    assert val2 == val


def test_property_with_multiple_instances(tmp_path):
    for i in range(3):
        (tmp_path / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    output.add_property("iout_int", lambda inst: int(inst.keys["iout"]))
    root.discover()

    instances = output.instances()
    values = sorted(inst.iout_int for inst in instances)
    assert values == [0, 1, 2]
