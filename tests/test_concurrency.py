"""Tests for concurrent property computation.

Covers two concurrency axes:
1. dask's threaded scheduler computing many instances in parallel (intra-call).
2. Multiple Python threads calling .get() simultaneously (inter-call).
3. dask "processes" scheduler: each property computed in a worker process.
"""

import threading
import time

import dask
import pytest
from sqlalchemy.orm import Session as SASession

import storyt as st
from storyt.db import Database, ObjectData

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_output(tmp_path):
    """Root with 10 output folders."""
    for i in range(1, 11):
        (tmp_path / f"output_{i:05d}").mkdir()
    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    return output


# ---------------------------------------------------------------------------
# 1. dask-internal parallelism
# ---------------------------------------------------------------------------


def test_dask_threaded_gives_correct_results(multi_output, tmp_path):
    """dask computes all 10 instance properties and returns correct values.

    A small sleep forces overlap when the threaded scheduler is active.
    """
    output = multi_output

    def _slow(inst):
        time.sleep(0.02)
        return int(inst.keys["iout"])

    output.add_property("iout_val", _slow)

    rows = output.all().get("iout_val")

    assert len(rows) == 10
    assert sorted(r["iout_val"] for r in rows) == list(range(1, 11))


def test_dask_threaded_property_chain(multi_output):
    """Dependent properties resolved correctly under dask threads."""
    output = multi_output

    output.add_property("base", lambda inst: int(inst.keys["iout"]))

    @output.add_property("derived", requires=["base"])
    def _derived(inst, base):
        time.sleep(0.01)
        return base * 10

    rows = output.all().get("derived")

    assert len(rows) == 10
    assert sorted(r["derived"] for r in rows) == [i * 10 for i in range(1, 11)]


# ---------------------------------------------------------------------------
# 2. Concurrent external threads calling .get() simultaneously
# ---------------------------------------------------------------------------


def test_two_threads_get_simultaneously(multi_output):
    """Two threads call .get() at the same time; both must get correct results."""
    output = multi_output
    output.add_property("doubled", lambda inst: int(inst.keys["iout"]) * 2)

    results: dict[str, list] = {}
    errors: dict[str, Exception] = {}

    def worker(name: str) -> None:
        try:
            results[name] = output.all().get("doubled")
        except Exception as exc:
            errors[name] = exc

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))

    # Start both threads before either has a chance to finish
    barrier = threading.Barrier(2)

    def worker_sync(name: str) -> None:
        barrier.wait()  # both threads enter .get() at the same instant
        try:
            results[name] = output.all().get("doubled")
        except Exception as exc:
            errors[name] = exc

    t1 = threading.Thread(target=worker_sync, args=("t1",))
    t2 = threading.Thread(target=worker_sync, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"Thread errors: {errors}"
    for name, rows in results.items():
        assert len(rows) == 10, f"{name}: expected 10 rows"
        assert sorted(r["doubled"] for r in rows) == [i * 2 for i in range(1, 11)]


def test_many_threads_get_simultaneously(multi_output):
    """Eight threads all call .get() concurrently; no corruption or deadlock."""
    output = multi_output
    output.add_property("tripled", lambda inst: int(inst.keys["iout"]) * 3)

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: dict[int, list] = {}
    errors: dict[int, Exception] = {}

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            results[idx] = output.all().get("tripled")
        except Exception as exc:
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Thread errors: {errors}"
    expected = [i * 3 for i in range(1, 11)]
    for idx, rows in results.items():
        assert len(rows) == 10, f"thread {idx}: expected 10 rows"
        assert sorted(r["tripled"] for r in rows) == expected


# ---------------------------------------------------------------------------
# 3. Cache correctness under concurrent writes
# ---------------------------------------------------------------------------


def test_concurrent_cache_not_corrupted(multi_output):
    """Property computed under concurrent access is cached with the right value."""
    output = multi_output
    call_count = {"n": 0}
    lock = threading.Lock()

    def _prop(inst):
        with lock:
            call_count["n"] += 1
        time.sleep(0.02)
        return int(inst.keys["iout"]) + 100

    output.add_property("shifted", _prop)

    # Two threads race to compute and cache the same property
    barrier = threading.Barrier(2)
    results: dict[str, list] = {}
    errors: dict[str, Exception] = {}

    def worker(name: str) -> None:
        barrier.wait()
        try:
            results[name] = output.all().get("shifted")
        except Exception as exc:
            errors[name] = exc

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"Thread errors: {errors}"
    expected = [i + 100 for i in range(1, 11)]
    for name, rows in results.items():
        assert sorted(r["shifted"] for r in rows) == expected, f"{name}: wrong values"

    # A third call should be fully served from cache — call_count stays the same
    count_after_concurrent = call_count["n"]
    output.all().get("shifted")
    assert call_count["n"] == count_after_concurrent, (
        "Cache miss after concurrent writes"
    )


# ---------------------------------------------------------------------------
# 4. dask "processes" scheduler
# ---------------------------------------------------------------------------


def _iout_val(inst):
    """Module-level function so it can be pickled by the processes scheduler."""
    return int(inst.keys["iout"])


def _iout_doubled(inst):
    return int(inst.keys["iout"]) * 2


def _iout_sum(inst, a, b):
    return a + b


def test_processes_scheduler_correct_results(tmp_path):
    """processes scheduler returns correct values for all instances.

    Uses a file-based DB (tmp_path/.storyt.db) which is required for the
    processes scheduler to share cached results across worker processes.
    """

    for i in range(1, 6):
        (tmp_path / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    output.add_property("iout_val", _iout_val)

    with dask.config.set(scheduler="processes", num_workers=2):
        rows = output.all().get("iout_val")

    assert len(rows) == 5
    assert sorted(r["iout_val"] for r in rows) == list(range(1, 6))


def test_processes_scheduler_property_chain(tmp_path):
    """Dependent properties resolve correctly under the processes scheduler."""

    for i in range(1, 4):
        (tmp_path / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    output.add_property("a", _iout_val)
    output.add_property("b", _iout_doubled)
    output.add_property("total", _iout_sum, requires=["a", "b"])

    with dask.config.set(scheduler="processes", num_workers=2):
        rows = output.all().get("total")

    # total = iout + iout*2 = iout*3
    assert sorted(r["total"] for r in rows) == [i * 3 for i in range(1, 4)]


def test_processes_scheduler_cache_persists(tmp_path):
    """After a processes run, results are cached in the on-disk DB.

    Worker processes write their results to the shared SQLite file; a follow-up
    call from the main process (synchronous scheduler, same function ⇒ same
    source hash) must be served entirely from the cache.
    """
    for i in range(1, 4):
        (tmp_path / f"output_{i:05d}").mkdir()

    root = st.StaticAsset(path=str(tmp_path), name="root")
    output = root.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    root.discover()
    output.add_property("iout_val", _iout_val)

    # First run: worker processes compute and write cache to the on-disk DB
    with dask.config.set(scheduler="processes", num_workers=2):
        rows1 = output.all().get("iout_val")

    assert sorted(r["iout_val"] for r in rows1) == list(range(1, 4))

    # Verify the on-disk DB has exactly one cached entry per instance
    db = Database(str(tmp_path / ".storyt.db"))
    with SASession(db.engine) as session:
        count = session.query(ObjectData).count()
    assert count == 3, f"Expected 3 cached entries in DB, got {count}"

    # Second run (synchronous, identical function)
    rows2 = output.all().get("iout_val")
    assert sorted(r["iout_val"] for r in rows2) == list(range(1, 4))


def test_processes_scheduler_in_memory_raises(tmp_path):
    """An in-memory Database cannot be pickled; a clear TypeError is raised."""

    db = Database(":memory:")
    with pytest.raises(TypeError, match="In-memory"):
        import pickle

        pickle.dumps(db)
