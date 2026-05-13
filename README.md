# storyt

`storyt` manages a hierarchy of static/dynamic assets (filesystem objects like simulation outputs) and caches computed properties in a SQLite database. It also ships a **web viewer** that exports the asset tree as a static site you can browse in any browser.

---

## Installation

```bash
pip install storyt
# or for development:
pip install -e .
```

---

## Quick start

```python
import storyt as st

project = st.StaticAsset(path="./MyProject", name="project")
sim = project.add_children(path=["run1", "run2"], name="simulation")
output = sim.add_children(re=r"output_(?P<iout>\d{5})", name="output")

project.discover()

for inst in output.instances(iout="00010"):
    print(inst.path, inst.keys)
```

---

## Key concepts

- **StaticAsset** — a node in the asset hierarchy (e.g. a project, a simulation, an output directory).
- **Instances** — concrete filesystem paths that match an asset's pattern.
- **Properties** — named computed values cached in SQLite (e.g. `gas_mass`, `stellar_mass`).
- **Bindings** — links between sibling assets that share a common key (e.g. `iout`).

---

## API reference

| Method | Description |
|---|---|
| `StaticAsset(path, name)` | Create a root asset at `path`. |
| `asset.add_children(path=…/re=…/callable, name=…)` | Add child asset type (static list, regex pattern, or callable). |
| `st.bind((a, "key"), (b, "key"), …)` | Declare a binding between siblings sharing a key. |
| `asset.reader(cls)` | Register a reader class for instances of this asset. |
| `asset.add_property(name, fn)` | Register a computed property (lazily cached). |
| `asset.discover()` | Walk the filesystem and populate instance records. |
| `asset.instances(**filters)` | Iterate instances (optionally filtered by key values). |
| `inst.get(prop_name)` / `inst.prop_name` | Compute and cache a property value. |

---

## Web viewer

### 1. Install frontend dependencies

```bash
cd storyt/viewer/frontend
npm install
```

### 2. Build the frontend

```bash
npm run build
```

This produces `storyt/viewer/frontend/dist/`.

### 3. Export from Python

```python
from pathlib import Path
from storyt.viewer import export_db

export_db(db=project._db, output_dir="./site", root_path=Path("./MyProject"))
```

This copies `dist/` contents and all `data/*.json` files into `./site/`.

### 4. Serve

```bash
cd site && python -m http.server 8080
```

Open <http://localhost:8080>.

---

## Demo

A full worked example with a mock simulation is in `tmp/build_demo.py`:

```bash
# activate venv first
source .venv/bin/activate

# build the demo site (builds frontend automatically if dist/ is missing)
python tmp/build_demo.py

# serve
cd tmp/site && python -m http.server 8080
```

---

## Development

### Running tests

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

### Frontend dev server

For live-reload development of the frontend UI:

```bash
cd storyt/viewer/frontend
npm run dev
```

> **Note:** the dev server needs a `data/` directory at the root of the served path to load hierarchy and instance data.  Copy fixture data or export a real database first.

---

## Database design

See [`docs/database.md`](docs/database.md) for the full SQLite schema.
