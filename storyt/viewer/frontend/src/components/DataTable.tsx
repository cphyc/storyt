import { PropertyRow } from "../types";

interface Props {
  rows: PropertyRow[];
}

export function DataTable({ rows }: Props) {
  return (
    <table>
      <thead>
        <tr>
          <th>Sibling</th>
          <th>Property</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            <td>{row.sibling}</td>
            <td>{row.property}</td>
            <td>{String(row.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
