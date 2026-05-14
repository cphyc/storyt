from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from storyt import db

root = db.Concept(name="root")
child = db.Concept(name="child", parent=root)

print(child)

engine = create_engine("sqlite:///:memory:", echo=True)
db.Base.metadata.create_all(engine)

with Session(engine) as session:
    # Define concepts
    simulation = db.Concept(name="simulation")
    timestep = simulation.add_child("timestep")
    halo = timestep.add_child("halos")

    session.add_all([simulation, timestep, halo])

    # Define resources
    output = timestep.add_resource("path", r"re:output_{\d{5}}/")
    halo_catalogue = timestep.add_resource(
        "halo_catalogue", r"re:halos/halos_{\d{5}}.pandas"
    )
    halo_instance = halo.add_resource("instance", lambda file_path: [])

    session.add(output)
    session.add(halo_catalogue)
    session.add(halo_instance)

    # Define products
    @halo_instance.add_product("SFR")
    def SFR(halo_instance):
        return 0

    session.add(SFR)
    session.commit()

    for concept in session.query(db.Concept).all():
        print(concept)
    for resource in session.query(db.Resource).all():
        print(resource)
    for product in session.query(db.Product).all():
        print(product)
