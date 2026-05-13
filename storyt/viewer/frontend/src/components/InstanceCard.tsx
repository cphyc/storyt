import { useState } from "react";
import { Instance, TreeNode, PropertyRow, SiblingEntry } from "../types";
import { cachedFetch } from "../fetchCache";
import { DataTable } from "./DataTable";

interface Props {
  instance: Instance;
  childrenTypes: TreeNode[];
  parentUrlPath: string | null;
  onNavigate: (inst: Instance, treeNode?: TreeNode) => void;
}

export function InstanceCard({
  instance,
  childrenTypes,
  parentUrlPath,
  onNavigate,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [propertyRows, setPropertyRows] = useState<PropertyRow[] | null>(null);
  const [loadingProps, setLoadingProps] = useState(false);

  const keyString = Object.entries(instance.keys || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
  const displayLabel = keyString || instance.url_path || String(instance.id);
  const canNavigate = !!(
    instance.url_path &&
    childrenTypes &&
    childrenTypes.length > 0
  );
  const siblings = instance.siblings || {};

  const fetchProperties = async () => {
    setLoadingProps(true);
    const rows: PropertyRow[] = [];
    const ownProps = [...(instance.properties || [])].sort((a, b) =>
      a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }),
    );

    for (const prop of ownProps) {
      const base = parentUrlPath ? `data/${parentUrlPath}` : "data";
      const data = (await cachedFetch(`${base}/${prop}.json`)) as Array<{
        id: number;
        value: unknown;
      }>;
      const entry = (data || []).find((e) => e.id === instance.id);
      rows.push({
        sibling: "(self)",
        property: prop,
        value: entry !== undefined ? entry.value : "N/A",
      });
    }

    const siblingEntries = (
      Object.entries(siblings) as [string, SiblingEntry][]
    ).sort(([nameA, sibA], [nameB, sibB]) => {
      const baseCmp = nameA.localeCompare(nameB, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (baseCmp !== 0) return baseCmp;
      const keyA = Object.entries(sibA.keys || {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      const keyB = Object.entries(sibB.keys || {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      return keyA.localeCompare(keyB, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });

    for (const [sibName, sib] of siblingEntries as [string, SiblingEntry][]) {
      const sibProps = [...(sib.properties || [])].sort((a, b) =>
        a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }),
      );
      for (const prop of sibProps) {
        // Property files live in the parent directory of the sibling's url_path
        const sibParent = sib.url_path
          ? sib.url_path.split("/").slice(0, -1).join("/")
          : null;
        const base = sibParent ? `data/${sibParent}` : "data";
        const data = (await cachedFetch(`${base}/${prop}.json`)) as Array<{
          id: number;
          value: unknown;
        }>;
        const entry = (data || []).find((e) => e.id === sib.id);
        rows.push({
          sibling: sibName,
          property: prop,
          value: entry !== undefined ? entry.value : "N/A",
        });
      }
    }

    rows.sort((a, b) => {
      const siblingCmp = a.sibling.localeCompare(b.sibling, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (siblingCmp !== 0) return siblingCmp;
      return a.property.localeCompare(b.property, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });

    setPropertyRows(rows);
    setLoadingProps(false);
  };

  const toggleExpand = async () => {
    const newExpanded = !expanded;
    setExpanded(newExpanded);
    if (newExpanded && propertyRows === null) {
      await fetchProperties();
    }
  };

  const siblingKeyStr = (sib: SiblingEntry) =>
    Object.entries(sib.keys || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");

  const sortedSiblings = (
    Object.entries(siblings) as [string, SiblingEntry][]
  ).sort(([nameA, sibA], [nameB, sibB]) => {
    const baseCmp = nameA.localeCompare(nameB, undefined, {
      numeric: true,
      sensitivity: "base",
    });
    if (baseCmp !== 0) return baseCmp;
    return siblingKeyStr(sibA).localeCompare(siblingKeyStr(sibB), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  });

  return (
    <div className="card">
      <div className="card-header">
        {canNavigate ? (
          <span className="nav-link" onClick={() => onNavigate(instance)}>
            {displayLabel}
          </span>
        ) : (
          <span className="key-display">{displayLabel}</span>
        )}
        <button data-testid="expand-btn" onClick={toggleExpand}>
          {expanded ? "▲ Collapse" : "▼ Expand"}
        </button>
      </div>
      {Object.keys(siblings).length > 0 && (
        <div>
          {sortedSiblings.map(([sibName, sib]) => {
            const sibHasChildren = (sib._treeNode?.children.length ?? 0) > 0;
            const sibInst: Instance = {
              id: sib.id ?? 0,
              keys: sib.keys,
              path: sib.path,
              url_path: sib.url_path,
            };
            return (
              <div key={sibName} className="sibling-section">
                <span className="sibling-name">{sibName}</span>
                {siblingKeyStr(sib) && <span>: {siblingKeyStr(sib)}</span>}
                {sibHasChildren && sib.url_path && (
                  <span
                    className="nav-link"
                    style={{ marginLeft: 8, fontSize: "12px" }}
                    onClick={() => onNavigate(sibInst, sib._treeNode)}
                  >
                    → open
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {expanded && (
        <div>
          {loadingProps ? (
            <span className="loading">Loading properties…</span>
          ) : propertyRows && propertyRows.length > 0 ? (
            <DataTable rows={propertyRows} />
          ) : (
            <p style={{ color: "#888", fontSize: "13px", margin: "4px 0" }}>
              No properties.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
