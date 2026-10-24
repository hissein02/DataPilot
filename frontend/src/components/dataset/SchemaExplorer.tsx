import type { DatasetTable } from '../../types/dataset'

export function SchemaExplorer({ table }: { table: DatasetTable }) {
  return <section className="schema-section" aria-labelledby="schema-title">
    <div className="section-heading"><h2 id="schema-title">Columns</h2></div>
    <div className="schema-header" aria-hidden="true"><span>Column</span><span>Type</span><span className="schema-distinct">Distinct</span><span>Missing</span></div>
    <div className="schema-list" role="region" aria-label="Column details" tabIndex={0}>{table.columns.map(column => <div className="schema-row" key={column.name}>
      <div className="schema-name"><strong>{column.display_name}</strong><code>{column.name}</code></div><span className="type-pill">{column.type}</span>
      <div className="schema-detail schema-distinct"><span className="sr-only">Distinct values: </span>{column.distinct_count.toLocaleString()}</div><div className="schema-detail schema-missing"><span className="sr-only">Missing: </span>{column.null_percentage}%</div>
    </div>)}</div>
  </section>
}
