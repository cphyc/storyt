"""Tests for the fluent Query API, property dependencies, and dask scheduling."""
import pytest
import storyt as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_hierarchy(tmp_path):
    """
    root/
      sim1/
        output_00001/
        output_00002/
      sim2/
        output_00001/
    """
    for sim in ["sim1", "sim2"]:
        for i in [1, 2] if sim == "sim1" else [1]:
            (tmp_path / sim / f"output_{i:05d}").mkdir(parents=True)

    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim1", "sim2"], name="sim")
    output = sim.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    return root, sim, output


# ---------------------------------------------------------------------------
# Fluent traversal
# ---------------------------------------------------------------------------

def test_asset_getattr_returns_child(tmp_path):
    root = st.StaticAsset(path=str(tmp_path), name="root")
    sim = root.add_children(path=["sim"], name="sim")
    assert root.sim is sim


def test_asset_getattr_unknown_raises(tmp_path):
    root = st.StaticAsset(path=str(tmp_path), name="root")
    with pytest.raises(AttributeError):
        _ = root.nonexistent


def test_all_returns_query(tmp_path):
    (tmp_path / "output_00001").mkdir()
    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()

    q = output.all()
    from storyt.query import Query
    assert isinstance(q, Query)
    assert len(q._instances) == 1


def test_chained_traversal(simple_hierarchy):
    root, sim, output = simple_hierarchy
    # sim.all() → all sim instances → traverse to output children
    q = sim.all().output
    assert len(q._instances) == 3  # 2 from sim1 + 1 from sim2


def test_all_is_idempotent(simple_hierarchy):
    root, sim, output = simple_hierarchy
    q = output.all().all().all()
    assert len(q._instances) == 3


# ---------------------------------------------------------------------------
# .query() filter
# ---------------------------------------------------------------------------

def test_query_filter_by_key(simple_hierarchy):
    root, sim, output = simple_hierarchy
    q = output.all().query(lambda inst: inst.keys.get("iout") == "00001")
    assert len(q._instances) == 2  # one per sim


def test_query_filter_empty(simple_hierarchy):
    root, sim, output = simple_hierarchy
    q = output.all().query(lambda inst: False)
    assert len(q._instances) == 0


def test_query_filter_with_property(simple_hierarchy):
    root, sim, output = simple_hierarchy

    # Assign a property that returns the iout integer
    output.add_property("iout_int", lambda inst: int(inst.keys["iout"]))

    q = output.all().query(lambda inst: inst.iout_int > 1)
    # Only output_00002 (iout=2), which exists only in sim1
    assert len(q._instances) == 1
    assert q._instances[0].keys["iout"] == "00002"


# ---------------------------------------------------------------------------
# .get() — simple (sequential)
# ---------------------------------------------------------------------------

def test_get_returns_list_of_dicts(simple_hierarchy):
    root, sim, output = simple_hierarchy
    output.add_property("iout_val", lambda inst: inst.keys["iout"])

    rows = output.all().get("iout_val")
    assert len(rows) == 3
    for row in rows:
        assert "path" in row
        assert "keys" in row
        assert "iout_val" in row


def test_get_values_are_correct(simple_hierarchy):
    root, sim, output = simple_hierarchy
    output.add_property("doubled", lambda inst: int(inst.keys["iout"]) * 2)

    rows = output.all().get("doubled")
    values = sorted(r["doubled"] for r in rows)
    assert values == [2, 2, 4]  # iout 1,1,2 → doubled 2,2,4


# ---------------------------------------------------------------------------
# Property dependencies (requires=)
# ---------------------------------------------------------------------------

def test_requires_simple(simple_hierarchy):
    root, sim, output = simple_hierarchy

    output.add_property("a", lambda inst: 3)
    output.add_property("b", lambda inst: 7)

    @output.add_property("c", requires=["a", "b"])
    def _c(inst, a, b):
        return a + b

    rows = output.all().get("c")
    assert all(r["c"] == 10 for r in rows)


def test_requires_chain(simple_hierarchy):
    root, sim, output = simple_hierarchy

    output.add_property("x", lambda inst: 2)

    @output.add_property("y", requires=["x"])
    def _y(inst, x):
        return x * 3

    @output.add_property("z", requires=["y"])
    def _z(inst, y):
        return y + 1

    rows = output.all().get("z")
    assert all(r["z"] == 7 for r in rows)  # (2*3)+1 = 7


def test_requires_dep_values_are_cached(simple_hierarchy):
    root, sim, output = simple_hierarchy

    call_count = {"n": 0}

    def _base(inst):
        call_count["n"] += 1
        return 42

    output.add_property("base", _base)

    @output.add_property("derived", requires=["base"])
    def _derived(inst, base):
        return base * 2

    # First access computes base once per instance, then derived
    rows = output.all().get("base", "derived")
    n_instances = len(rows)
    assert call_count["n"] == n_instances

    # Second access: all cached, call_count unchanged
    output.all().get("base", "derived")
    assert call_count["n"] == n_instances


def test_circular_dependency_raises(simple_hierarchy):
    root, sim, output = simple_hierarchy

    output.add_property("p", lambda inst: 1, requires=["q"])
    output.add_property("q", lambda inst: 2, requires=["p"])

    with pytest.raises(RuntimeError, match="[Cc]ircular"):
        output.all().get("p")


# ---------------------------------------------------------------------------
# .get() with chained traversal
# ---------------------------------------------------------------------------

def test_get_after_chain(simple_hierarchy):
    root, sim, output = simple_hierarchy
    output.add_property("label", lambda inst: inst.keys["iout"])

    rows = sim.all().output.get("label")
    assert len(rows) == 3
    labels = {r["label"] for r in rows}
    assert labels == {"00001", "00002"}
