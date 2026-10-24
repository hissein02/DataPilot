import { useRef, type FormEvent, type KeyboardEvent } from 'react'
import { ChevronDown } from 'lucide-react'
import type { Analysis, AnalysisEvent, DatasetTable } from '../../types/dataset'

type Progress = Extract<AnalysisEvent, { type: 'progress' }>
export type QuestionRecord = {
  id: number
  question: string
  table: string
  tableLabel: string
  context: string
  createdAt: number
  progress: Progress[]
  status: 'working' | 'complete' | 'failed'
  result?: Analysis
  error?: string
}

type NewProps = { table: DatasetTable; draft: string; onDraft: (value: string) => void; onAsk: (value: string) => void; busy: boolean; record?: never }
type AnswerProps = { table: DatasetTable; record: QuestionRecord; draft?: never; onDraft?: never; onAsk?: never; busy?: never }

export function QuestionPage(props: NewProps | AnswerProps) {
  const form = useRef<HTMLFormElement>(null)
  const { table, record } = props

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!record) props.onAsk(props.draft)
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      form.current?.requestSubmit()
    }
  }

  if (!record) return <section className="question-page new-page" aria-labelledby="new-question-title">
    <h1 id="new-question-title">New question</h1>
    <p className="page-intro">About {table.display_name}. Previous answers are not included.</p>
    <form className="single-question-form" ref={form} onSubmit={submit}><label htmlFor="new-question-input">Question</label><textarea id="new-question-input" value={props.draft} onChange={event => props.onDraft(event.target.value)} onKeyDown={keyDown} rows={5} maxLength={2000} autoFocus /><div className="question-form-footer"><button type="submit" disabled={!props.draft.trim() || props.busy}>Ask question</button></div></form>
  </section>

  const events = record.result?.trace.length ? record.result.trace : record.progress
  return <article className="question-page answer-page" aria-labelledby="question-title">
    <div className="answer-page-header"><p className="eyebrow">{record.tableLabel}</p><h1 id="question-title">{record.question}</h1></div>
    {record.context && <details className="used-context"><summary>Dataset context used <ChevronDown size={14} aria-hidden="true" /></summary><p>{record.context}</p></details>}
    {record.error && <section className="result-section answer-result" aria-labelledby="answer-title"><h2 id="answer-title">Answer</h2><p className="analysis-error" role="alert">{record.error}</p></section>}
    {record.result && <AnalysisAnswer result={record.result} />}
    {(record.status === 'working' || events.length > 0) && <section className="process-panel" aria-label="Analysis process">
      {record.status === 'working' && <p className="analysis-progress" role="status">{record.progress.at(-1)?.message ?? 'Planning the analysis…'}</p>}
      {events.length > 0 && <ActivityTrace events={events} live={record.status === 'working'} />}
    </section>}
  </article>
}

function ActivityTrace({ events, live }: { events: { step: string; status: string; message: string }[]; live: boolean }) {
  return <details className="activity-trace" open={live ? true : undefined}><summary>Analysis process <ChevronDown size={15} aria-hidden="true" /></summary><ol>{events.map((entry, index) => <li key={index} className={`activity-${entry.status}`}><span>{entry.step.replaceAll('_', ' ')}</span><p>{entry.message}</p></li>)}</ol></details>
}

function AnalysisAnswer({ result }: { result: Analysis }) {
  const queries = result.queries.length ? result.queries : result.sql ? [{ purpose: 'Supporting query', sql: result.sql, columns: result.columns, rows: result.rows, truncated: result.truncated, execution_duration_ms: result.execution_duration_ms }] : []
  return <div className="analysis-answer"><section className="result-section answer-result" aria-labelledby="answer-title"><h2 id="answer-title">Answer</h2><p className="answer-text">{result.answer}</p>
    {result.limitation && <p className="answer-limitation">{result.limitation}</p>}
  </section>
    {queries.length > 0 && <section className="result-section supporting-section" aria-labelledby="supporting-title"><h2 id="supporting-title">Supporting results</h2><div className="evidence-list">{queries.map((query, index) => <details className="query-evidence" key={`${query.purpose}-${index}`}><summary><span>{query.purpose}</span><small>{query.rows.length}{query.truncated ? '+' : ''} {query.rows.length === 1 ? 'row' : 'rows'}</small><ChevronDown size={15} aria-hidden="true" /></summary><div className="evidence-content">
      {query.rows.length > 0 && <div className="table-scroll"><table><thead><tr>{query.columns.map(column => <th scope="col" key={column}>{column}</th>)}</tr></thead><tbody>{query.rows.map((row, rowIndex) => <tr key={rowIndex}>{query.columns.map(column => <td key={column}>{row[column] == null ? <span className="null-cell">—</span> : String(row[column])}</td>)}</tr>)}</tbody></table></div>}
      {query.rows.length === 0 && <p className="table-status">No matching rows.</p>}
      <details className="sql-details"><summary>View executed SQL</summary><pre><code>{query.sql}</code></pre></details>
    </div></details>)}</div></section>}
  </div>
}
