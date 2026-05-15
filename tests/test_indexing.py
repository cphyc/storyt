import pytest
from sqlalchemy import create_engine

import storyt as st


@pytest.fixture
def story():
    engine = create_engine("sqlite:///:memory:", echo=True)
    st.db.Base.metadata.create_all(engine)
    story = st.Story(engine)
    yield story


@pytest.fixture
def file_hierarchy(tmpdir):
    hierarchy = [
        "simulation_{:02d}",
        "output_{:05d}",
        "halos_{:d}.csv",
    ]

    children_per_level = 11
    global_counter = 0
    # Create a depth=3 file hierarchy
    stack = [tmpdir]
    for template in hierarchy[:-1]:
        new_stack = []
        for parent in stack:
            for ichild in range(children_per_level):
                child = parent.mkdir(template.format(ichild))
                global_counter += 1
                new_stack.append(child)

        stack = new_stack

    # Create some files at the deepest level
    for folder in stack:
        for ichild in range(children_per_level):
            (folder / hierarchy[-1].format(ichild)).write("col1,col2\n1,2\n3,4")

    return tmpdir


def test_indexing(story, file_hierarchy):
    with story.record() as r:
        # Concepts
        sim = r.Concept(name="simulation")
        output = sim.add_child("output")
        halo = output.add_child("halo")

        # Resources
        sim_folder = sim.add_resource("folder", file_hierarchy)

        output_folder = (sim_folder > output).glob("output_*", name="output_folder")

        halo_files = (output_folder > halo).glob("halos_*.csv")

        halo_files.discover()

        with pytest.raises(ValueError):
            # Because a simulation cannot have a halo directly
            _ = sim_folder > halo
