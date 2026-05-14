"""
Demo script: build a mock simulation, populate the storyt database,
compute some properties, and export a static viewer to example/site/.

Run from the project root:
    python example/build_demo.py

Then serve:
    cd example/site && python -m http.server 8080
and open http://localhost:8080
"""

from __future__ import annotations

import pathlib
import shutil

import numpy as np

import storyt as st
from storyt.viewer import export_db

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = pathlib.Path(__file__).parent.resolve()
SIM_ROOT = HERE / "sim"
SITE_DIR = HERE / "site"

# ---------------------------------------------------------------------------
# 1. Build the mock directory / file structure
# ---------------------------------------------------------------------------


def build_mock_simulation(base: pathlib.Path) -> None:
    """
    base/
      MEGATRON_CP_NEW/
        output_00001/  output_00002/  output_00003/
        rockstar_halos/halos_00001.ascii  …00002  …00003
        halo_cutouts/output_00001/halo_0001_gas.bin  halo_0002_gas.bin
                    /output_00002/…
                    /output_00003/…
      MEGATRON_ISM/
        output_00001/  output_00002/
        rockstar_halos/halos_00001.ascii  …00002
        halo_cutouts/output_00001/halo_0001_gas.bin  halo_0002_gas.bin
                    /output_00002/…
    """
    rng = np.random.default_rng(42)
    sims = {
        "MEGATRON_CP_NEW": ["00001", "00002", "00003"],
        "MEGATRON_ISM": ["00001", "00002"],
    }
    halos = ["0001", "0002", "0003"]

    for sim, iouts in sims.items():
        for iout in iouts:
            (base / sim / f"output_{iout}").mkdir(parents=True, exist_ok=True)

            # halo catalogue (CSV)
            cat = base / sim / "rockstar_halos" / f"halos_{iout}.ascii"
            cat.parent.mkdir(parents=True, exist_ok=True)
            cat.write_text(
                "id,x,y,z,mass\n"
                + "\n".join(
                    f"{hid},{rng.uniform():.3f},{rng.uniform():.3f},"
                    f"{rng.uniform():.3f},{rng.uniform(1e11, 1e13):.4e}"
                    for hid in halos
                )
                + "\n"
            )

            # cutout binary files
            for hid in halos:
                cutout = (
                    base
                    / sim
                    / "halo_cutouts"
                    / f"output_{iout}"
                    / f"halo_{hid}_gas.bin"
                )
                cutout.parent.mkdir(parents=True, exist_ok=True)
                # [n_particles, gas_mass_per_particle, stellar_mass_per_particle]
                n_part = rng.integers(50, 200)
                data = rng.uniform(0.01, 1.0, size=(n_part, 3)).astype(np.float32)
                cutout.write_bytes(data.tobytes())


# ---------------------------------------------------------------------------
# 2. Minimal data readers
# ---------------------------------------------------------------------------


class FakeCatalogue:
    def __init__(self, path: pathlib.Path):
        lines = path.read_text().splitlines()
        header = lines[0].split(",")
        self._rows = [
            dict(zip(header, ln.split(","), strict=False)) for ln in lines[1:] if ln
        ]

    def iterrows(self):
        for row in self._rows:
            yield row["id"], row


class FakeCutout:
    def __init__(self, path: pathlib.Path):
        raw = np.frombuffer(path.read_bytes(), dtype=np.float32)
        self._data = raw.reshape(-1, 3)

    @property
    def gas_mass(self) -> float:
        return float(self._data[:, 1].sum())

    @property
    def stellar_mass(self) -> float:
        return float(self._data[:, 2].sum())


# ---------------------------------------------------------------------------
# 3. Build the storyt hierarchy
# ---------------------------------------------------------------------------


def setup_hierarchy(root: pathlib.Path) -> st.StaticAsset:
    project = st.StaticAsset(path=str(root), name="project")

    sim = project.add_children(
        path=["MEGATRON_CP_NEW", "MEGATRON_ISM"], name="simulation"
    )
    output = sim.add_children(re=r"output_(?P<iout>\d{5})", name="output")
    halo_cat = sim.add_children(
        re=r"rockstar_halos/halos_(?P<iout>\d{5})\.ascii", name="halo_catalogue"
    )
    cutout_folder = sim.add_children(
        re=r"halo_cutouts/output_(?P<iout>\d{5})", name="cutout_folder"
    )

    st.bind((output, "iout"), (halo_cat, "iout"), (cutout_folder, "iout"))

    cutout = cutout_folder.add_children(
        re=r"halo_(?P<halo_id>\d{4})_gas\.bin", name="cutout"
    )

    @halo_cat.register_reader(name="catalogue")
    def catalogue_reader(inst):
        return FakeCatalogue(inst.path)

    @cutout.register_reader(name="ds")
    def ds(inst):
        return FakeCutout(inst.path)

    @cutout.register_reader(requires="ds")
    def sp(inst):
        return inst.reader["ds"]

    halo = halo_cat.add_children(lambda cat: cat.iterrows(), name="halo", key="halo_id")
    st.bind((halo, "halo_id"), (cutout, "halo_id"))

    # Properties on individual cutouts
    cutout.add_property("gas_mass", lambda ds: ds.gas_mass, reader="ds")
    cutout.add_property(
        "stellar_mass",
        lambda ds: ds.stellar_mass,
        reader="ds",
    )

    @cutout.add_property("gas_to_stellar_ratio", requires=["gas_mass", "stellar_mass"])
    def _ratio(c, gas_mass, stellar_mass):
        return gas_mass / stellar_mass if stellar_mass > 0 else float("nan")

    return project, sim, output, halo_cat, cutout_folder, cutout, halo


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Wipe and recreate sim tree (idempotent re-runs)
    if SIM_ROOT.exists():
        shutil.rmtree(SIM_ROOT)
    SIM_ROOT.mkdir()

    print("Building mock simulation on disk…")
    build_mock_simulation(SIM_ROOT)

    print("Setting up storyt hierarchy…")
    project, sim, output, halo_cat, cutout_folder, cutout, halo = setup_hierarchy(
        SIM_ROOT
    )

    print("Discovering instances…")
    project.discover()

    print(f"  simulations   : {len(sim.instances())}")
    print(f"  outputs       : {len(output.instances())}")
    print(f"  halo cats     : {len(halo_cat.instances())}")
    print(f"  cutout folders: {len(cutout_folder.instances())}")
    print(f"  cutouts       : {len(cutout.instances())}")

    # Compute properties for MEGATRON_CP_NEW only (ISM left blank → shows N/A)
    print("\nComputing properties for MEGATRON_CP_NEW cutouts…")
    cp_cutouts = [c for c in cutout.instances() if "MEGATRON_CP_NEW" in str(c.path)]
    for inst in cp_cutouts:
        _ = inst.gas_mass
        _ = inst.stellar_mass
        _ = inst.gas_to_stellar_ratio

    print(f"  computed {len(cp_cutouts)} cutout instances")

    # Build frontend if needed
    frontend_dir = (
        pathlib.Path(__file__).parent.parent / "storyt" / "viewer" / "frontend"
    )
    dist_dir = frontend_dir / "dist"
    if not dist_dir.exists():
        print("\nBuilding frontend (npm run build)…")
        import subprocess

        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)

    # Export static viewer
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)

    print(f"\nExporting viewer to {SITE_DIR} …")
    export_db(db=project._db, output_dir=SITE_DIR, root_path=SIM_ROOT)

    print("\n✓ Done!")
    print(f"\n  Serve with:\n    cd {SITE_DIR} && python -m http.server 8080")
    print("  Then open:  http://localhost:8080")


if __name__ == "__main__":
    main()
