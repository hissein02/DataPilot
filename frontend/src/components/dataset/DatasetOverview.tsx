import type { DatasetTable } from '../../types/dataset'

export function DatasetOverview({ table }: { table: DatasetTable }) {
  return <section className="overview" aria-labelledby="overview-title">
    <h1 id="overview-title">{table.display_name}</h1>
    {table.warnings.length > 0 && <div className="import-notes"><strong>Import notes</strong><ul>{table.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></div>}
  </section>
}
