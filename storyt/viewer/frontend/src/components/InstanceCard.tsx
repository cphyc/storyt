import { useState } from "react";
import { Instance, TreeNode, PropertyRow, SiblingEntry } from "../types";
import { cachedFetch } from "../fetchCache";
import { DataTable } from "./DataTable";

interface Props {
  instance: Instance;
  childrenTypes: TreeNode[];
  parentUrlPath: string | null;
  onNavigate: (inst: Instance) => void;
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

    for (const prop of instance.properties || []) {
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

    for (const [sibName, sib] of Object.entries(siblings) as [
      string,
      SiblingEntry,
    ][]) {
      for (const prop of sib.properties || []) {
        const base = sib.url_path ? `data/${sib.url_path}` : "data";
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
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");

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
          {(Object.entries(siblings) as [string, SiblingEntry][]).map(
            ([sibName, sib]) => (
              <div key={sibName} className="sibling-section">
                <span className="sibling-name">{sibName}</span>
                {siblingKeyStr(sib) && <span>: {siblingKeyStr(sib)}</span>}
              </div>
            ),
          )}
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
