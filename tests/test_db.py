import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

import storyt as st


@pytest.fixture
def story():
    engine = create_engine("sqlite:///:memory:")
    st.db.Base.metadata.create_all(engine)
    story = st.Story(engine)
    yield story


def test_concept(story):
    with story.record() as r:
        root = r.Concept(name="test_concept")
        c3 = root.add_child("c1").add_child("c2").add_child("c3")

    # Those should work
    _c2 = c3.parent
    _c1 = c3.parent.parent
    root_copy = c3.parent.parent.parent

    # Third level parent should be root
    assert root_copy == root


def test_resource(story):
    with story.record() as r:
        root = r.Concept(name="test_resource")
        subfolder = root.add_child("subfolder")

        r1 = root.add_resource("resource1", "/foo")
        r2 = root.add_resource("resource2", "/bar")

        r3 = (r1 > subfolder).glob("*", name="glob_resource")
        r4 = (r2 > subfolder).re("*", name="re_resource")

    # Can't add a resource with the same name and same concept
    with pytest.raises(IntegrityError):
        with story.record() as r:
            # Duplicate!
            root.add_resource("resource1", "/foobar")

    # Those should work
    for r, k in zip(
        (r1, r2),
        (st.db.ResourceKind.PATH, st.db.ResourceKind.PATH),
        strict=True,
    ):
        assert r.concept == root
        assert r.source_code is not None
        assert r.kind is k

    assert r3.kind == st.types.ResourceKind.GLOB
    assert r3.parent == r1
    assert r3.concept == subfolder

    assert r4.kind == st.types.ResourceKind.RE
    assert r4.parent == r2
    assert r4.concept == subfolder


def test_product(story):

    with story.record() as r:
        root = r.Concept(name="test_product")
        resource = root.add_resource("resource1", "/foo")

        @resource.add_product("times2")
        def times2(x):
            return x * 2

    # Those should work
    assert times2.name == "times2"
    assert times2.resource == resource

    # It should be callable as well
    assert times2(2) == 4

    # Try reading from database
    p = story.session.query(st.db.Product).filter_by(name="times2").one()
    assert p.name == "times2"
    assert p.source_code is not None

    # Those should be comparable
    assert p == times2

    # Make sure that we can call p
    assert p(3) == 6
