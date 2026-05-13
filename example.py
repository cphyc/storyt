"""
storit example — self-contained demo using a dummy simulation tree.

Run with:
    python example.py
"""
import pathlib
import tempfile

import numpy as np

import storyt as st


# ---------------------------------------------------------------------------
# 1.  Build a dummy directory/file structure
# ---------------------------------------------------------------------------

def create_dummy_project(base: pathlib.Path) -> None:
    """
    base/
      MEGATRON_CP_NEW/
        output_00001/  output_00002/  output_00003/
        rockstar_halos/halos_00001.ascii  (…00002, …00003)
        halo_cutouts/output_00001/halo_0001_gas.bin  (…0002)
                    /output_00002/halo_0001_gas.bin  (…0002)
                    /output_00003/halo_0001_gas.bin  (…0002)
      MEGATRON_ISM/
        output_00001/  output_00002/
        rockstar_halos/halos_00001.ascii  (…00002)
        halo_cutouts/output_00001/halo_0001_gas.bin  (…0002)
                    /output_00002/halo_0001_gas.bin  (…0002)
    """
    sims = {
        "MEGATRON_CP_NEW": ["00001", "00002", "00003"],
        "MEGATRON_ISM":    ["00001", "00002"],
    }
    halo_ids = ["0001", "0002"]

    for sim, iouts in sims.items():
        for iout in iouts:
            (base / sim / f"output_{iout}").mkdir(parents=True)

            catalogue = base / sim / "rockstar_halos" / f"halos_{iout}.ascii"
            catalogue.parent.mkdir(parents=True, exist_ok=True)
            catalogue.write_text(
                "id,x,y,z,mass\n"
                + "\n".join(
                    f"{hid},1.0,2.0,3.0,{(i + 1) * 1e12}"
                    for i, hid in enumerate(halo_ids)
                )
                + "\n"
            )

            for hid in halo_ids:
                cutout = (
                    base / sim / "halo_cutouts" / f"output_{iout}" / f"halo_{hid}_gas.bin"
                )
                cutout.parent.mkdir(parents=True, exist_ok=True)
                cutout.write_bytes(
                    np.array([float(hid), 2.0, 3.0], dtype=np.float32).tobytes()
                )


# ---------------------------------------------------------------------------
# 2.  Dummy data models (stand-ins for yt / pandas)
# ---------------------------------------------------------------------------

class FakeCatalogue:
    """Minimal stand-in for a pandas DataFrame loaded from CSV."""

    def __init__(self, path: pathlib.Path):
        lines = path.read_text().splitlines()
        header = lines[0].split(",")
        self._rows = [dict(zip(header, ln.split(","))) for ln in lines[1:] if ln]

    def iterrows(self):
        for row in self._rows:
            yield row["id"], row


class FakeCutout:
    """Stand-in for a loaded simulation cutout (e.g. a particle snapshot)."""

    def __init__(self, path: pathlib.Path):
        self._data = np.frombuffer(path.read_bytes(), dtype=np.float32)

    @property
    def gas_mass(self) -> float:
        return float(self._data.sum())

    @property
    def stellar_mass(self) -> float:
        return float(self._data.mean() * 0.1)


# ---------------------------------------------------------------------------
# 3.  Define the asset hierarchy
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="storit_example_") as tmpdir:
    base = pathlib.Path(tmpdir)
    create_dummy_project(base)

    project = st.StaticAsset(path=str(base), name="project")
    sim = project.add_children(
        path=["MEGATRON_CP_NEW", "MEGATRON_ISM"], name="simulation"
    )

    # Three child types per simulation, each matched by a regex
    output = sim.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    halo_catalogue = sim.add_children(
        re=r"rockstar_halos/halos_(?P<iout>\d{5})\.ascii", name="halo_catalogue"
    )
    cutout_folder = sim.add_children(
        re=r"halo_cutouts/output_(?P<iout>\d{5})", name="cutout_folder"
    )

    # output, halo_catalogue, and cutout_folder are linked by "iout"
    st.bind((output, "iout"), (cutout_folder, "iout"), (halo_catalogue, "iout"))

    # Each cutout folder contains one binary file per halo
    cutout = cutout_folder.add_children(
        re=r"halo_(?P<halo_id>\d{4})_gas\.bin", name="cutout"
    )

    # Readers (would be yt.load / pandas.read_csv in production)
    output.reader(lambda path: path)
    halo_catalogue.reader(FakeCatalogue)
    cutout.reader(FakeCutout)

    # Dynamic children: one AssetInstance per row in the catalogue
    halo = halo_catalogue.add_children(
        lambda catalogue: catalogue.iterrows(), name="halo", key="halo_id"
    )
    st.bind((halo, "halo_id"), (cutout, "halo_id"))

    # ---------------------------------------------------------------------------
    # 4.  Register properties
    # ---------------------------------------------------------------------------

    cutout.add_property("total_gas_mass",     lambda c: c.load().gas_mass)
    cutout.add_property("total_stellar_mass", lambda c: c.load().stellar_mass)

    @cutout.add_property("SFR")
    def _SFR(c):
        data = c.load()._data
        counts, edges = np.histogram(data, bins=np.linspace(0, 10, 5))
        return counts.tolist()

    @cutout.add_property("gas_to_star_ratio",
                         requires=["total_gas_mass", "total_stellar_mass"])
    def _ratio(c, gas_mass, stellar_mass):
        return gas_mass / stellar_mass

    # ---------------------------------------------------------------------------
    # 5.  Discover instances on disk
    # ---------------------------------------------------------------------------

    project.discover()

    print("=== Discovered instances ===")
    print(f"  Simulations  : {len(sim.instances())}")
    print(f"  Outputs      : {len(output.instances())}")
    print(f"  Catalogues   : {len(halo_catalogue.instances())}")
    print(f"  Cutout dirs  : {len(cutout_folder.instances())}")
    print(f"  Cutout files : {len(cutout.instances())}")

    # ---------------------------------------------------------------------------
    # 6.  Fluent API
    # ---------------------------------------------------------------------------

    print("\n=== Fluent traversal ===")

    # All cutouts from MEGATRON_CP_NEW (3 iouts × 2 halos = 6)
    cp_cutouts = (
        sim.all()
        .query(lambda s: "CP_NEW" in str(s.path))
        .cutout_folder.all()
        .cutout
    )
    print(f"  MEGATRON_CP_NEW cutouts : {len(cp_cutouts._instances)}")

    # Only iout=00001 across both sims (2 sims × 2 halos = 4)
    iout1_cutouts = (
        cutout_folder.all()
        .query(lambda cf: cf.keys["iout"] == "00001")
        .cutout
    )
    print(f"  iout=00001 cutouts      : {len(iout1_cutouts._instances)}")

    # Top-level chain from the original API sketch:
    #   sim.output.all().cutout_folder.all().cutout.all()
    #       .get("SFR", "total_gas_mass", "total_stellar_mass")
    print("\n=== .get() over iout=00001 cutouts ===")
    rows = iout1_cutouts.get("total_gas_mass", "total_stellar_mass", "gas_to_star_ratio")
    for row in rows:
        print(
            f"  halo_id={row['keys']['halo_id']}"
            f"  gas={row['total_gas_mass']:.2f}"
            f"  star={row['total_stellar_mass']:.3f}"
            f"  ratio={row['gas_to_star_ratio']:.1f}"
        )

    # Bound partners: given one output, find its cutout_folder
    first_output = output.instances(iout="00001")[0]
    bound_cf = first_output.bound("cutout_folder")
    print(f"\n=== Binding: output(iout=00001) → {len(bound_cf)} cutout_folder(s) ===")
    for cf in bound_cf:
        print(f"  {cf.path.relative_to(base)}")

    print("\nDone.")
