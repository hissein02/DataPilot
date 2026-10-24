import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { DatasetTable, Preview } from '../../types/dataset'

type Props = { table: DatasetTable; preview: Preview | null; offset: number; loading: boolean; onOffset: (offset: number) => void }
export function DataPreview({ table, preview, offset, loading, onOffset }: Props) {
  const pageSize = 10
  const end = Math.min(offset + pageSize, table.row_count)
  return <section className="preview-section" aria-labelledby="preview-title">
    <div className="section-heading"><h2 id="preview-title">Preview</h2><span className="section-count">{table.row_count ? `${offset + 1}–${end} of ${table.row_count.toLocaleString()}` : '0 rows'}</span></div>
    <div className="table-scroll"><table><thead><tr>{table.columns.map(column => <th key={column.name} scope="col">{column.display_name}</th>)}</tr></thead><tbody>{preview?.rows.map((row, index) => <tr key={offset + index}>{table.columns.map(column => <td key={column.name}>{row[column.name] === null || row[column.name] === undefined ? <span className="null-cell">—</span> : String(row[column.name])}</td>)}</tr>)}</tbody></table>
      {loading && <p className="table-status" role="status">Loading preview…</p>}{!loading && preview?.rows.length === 0 && <p className="table-status">No rows to preview.</p>}
    </div>
    <div className="pagination"><button type="button" aria-label="Previous page" disabled={offset === 0 || loading} onClick={() => onOffset(Math.max(0, offset - pageSize))}><ChevronLeft size={18} /></button><span>Page {Math.floor(offset / pageSize) + 1}</span><button type="button" aria-label="Next page" disabled={end >= table.row_count || loading} onClick={() => onOffset(offset + pageSize)}><ChevronRight size={18} /></button></div>
  </section>
}
