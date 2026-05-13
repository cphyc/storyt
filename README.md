# storyt

`storyt` manages a hierarchy of static/dynamic assets (filesystem objects like simulation outputs)
and caches computed properties in a SQLite database.

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
