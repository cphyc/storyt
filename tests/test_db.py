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

        r1 = root.add_resource("resource1", lambda x: x)
        r2 = root.add_resource("resource2", "glob:*.txt")
        r3 = root.add_resource("resource3", r"re:.*\.txt")

    # Can't add a resource with the same name and same concept
    with pytest.raises(IntegrityError):
        with story.record() as r:
            # Duplicate!
            root.add_resource("resource1", lambda x: x)

    with story.record() as r:
        # But we can add a resource with the same name and
        # a different concept
        subfolder.add_resource("resource1", lambda x: x)

    # Those should work
    for r, k in zip(
        (r1, r2, r3),
        (st.db.ResourceKind.FUNCTION, st.db.ResourceKind.GLOB, st.db.ResourceKind.RE),
        strict=True,
    ):
        assert r.concept == root
        assert r.source_code is not None
        assert r.kind is k


def test_product(story):
    with story.record() as r:
        root = r.Concept(name="test_product")
        resource = root.add_resource("resource1", lambda x: x)

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
