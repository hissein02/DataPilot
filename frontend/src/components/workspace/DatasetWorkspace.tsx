import { useEffect, useRef, useState } from 'react'
import { QuestionPage, type QuestionRecord } from '../analysis/QuestionPage'
import { DataPreview } from '../dataset/DataPreview'
import { DatasetOverview } from '../dataset/DatasetOverview'
import { SchemaExplorer } from '../dataset/SchemaExplorer'
import { useDataset } from '../../hooks/useDataset'
import { analyzeDatasetStream } from '../../lib/api'

type View = { kind: 'dataset' } | { kind: 'new' } | { kind: 'question'; id: number }

type DatasetState = ReturnType<typeof useDataset>
type Table = NonNullable<DatasetState['dataset']>['tables'][number]

export function DatasetWorkspace({ state, table }: { state: DatasetState; table: Table }) {
  const dataset = state.dataset!
  const storageKey = `datapilot.questions.${dataset.workspace_id}`
  const contextKey = `datapilot.context.${dataset.workspace_id}`
  const [view, setView] = useState<View>({ kind: 'dataset' })
  const [datasetContext, setDatasetContext] = useState(() => sessionStorage.getItem(contextKey) ?? '')
  const [draft, setDraft] = useState('')
  const [questions, setQuestions] = useState<QuestionRecord[]>(() => {
    try {
      const stored: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? '[]')
      if (!Array.isArray(stored)) return []
      return stored.filter((item): item is QuestionRecord => typeof item === 'object' && item !== null && typeof item.id === 'number' && typeof item.question === 'string' && typeof item.table === 'string' && Array.isArray(item.progress) && ['working', 'complete', 'failed'].includes(item.status)).map(item => item.status === 'working' ? { ...item, status: 'failed' as const, error: 'This question was interrupted by a page reload.' } : item)
    } catch { return [] }
  })
  const sequence = useRef(Math.max(0, ...questions.map(item => item.id)))
  const requests = useRef(new Set<AbortController>())

  useEffect(() => () => { for (const request of requests.current) request.abort() }, [])
  useEffect(() => { sessionStorage.setItem(contextKey, datasetContext) }, [contextKey, datasetContext])
  useEffect(() => { try { sessionStorage.setItem(storageKey, JSON.stringify(questions.slice(0, 30))) } catch { /* Browser storage can be full; current page remains usable. */ } }, [storageKey, questions])

  const selected = view.kind === 'question' ? questions.find(item => item.id === view.id) : undefined
  const running = questions.some(item => item.status === 'working')

  async function ask(value: string) {
    const question = value.trim()
    if (!question || running) return
    const record: QuestionRecord = {
      id: ++sequence.current,
      question,
      table: table.name,
      tableLabel: table.display_name,
      context: datasetContext.trim(),
      createdAt: Date.now(),
      progress: [],
      status: 'working',
    }
    const controller = new AbortController()
    requests.current.add(controller)
    setQuestions(current => [record, ...current])
    setDraft('')
    setView({ kind: 'question', id: record.id })
    let receivedResult = false
    try {
      for await (const event of analyzeDatasetStream(dataset.workspace_id, record.table, record.question, record.context, controller.signal)) {
        if (event.type === 'progress') setQuestions(current => current.map(item => item.id === record.id ? { ...item, progress: [...item.progress, event] } : item))
        if (event.type === 'result') {
          receivedResult = true
          setQuestions(current => current.map(item => item.id === record.id ? { ...item, result: event.report, status: 'complete' } : item))
        }
        if (event.type === 'error') throw new Error(event.message)
      }
      if (!receivedResult) throw new Error('Analysis ended without a result. Try again.')
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = cause instanceof Error ? cause.message : 'Analysis failed. Try again.'
        setQuestions(current => current.map(item => item.id === record.id ? { ...item, error, status: 'failed' } : item))
      }
    } finally { requests.current.delete(controller) }
  }

  return <div className="question-workspace">
    <aside className="question-menu" aria-label="Workspace menu">
      <div className="menu-heading"><strong>{dataset.filename}</strong></div>
      {dataset.tables.length > 1 && <div className="menu-table"><label htmlFor="workspace-table">Table</label><select id="workspace-table" value={state.activeTable} onChange={event => { state.selectTable(event.target.value); setView({ kind: 'dataset' }) }}>{dataset.tables.map(item => <option key={item.name} value={item.name}>{item.display_name}</option>)}</select></div>}
      <nav aria-label="Workspace pages" className="menu-pages"><button type="button" className={view.kind === 'dataset' ? 'active' : ''} aria-current={view.kind === 'dataset' ? 'page' : undefined} onClick={() => setView({ kind: 'dataset' })}>Dataset</button></nav>
      <p className="menu-stats" aria-label="Dataset statistics">{table.row_count.toLocaleString()} rows · {table.column_count.toLocaleString()} columns</p>
      <details className="menu-context"><summary>Dataset context <span>{datasetContext ? 'Added' : 'Optional'}</span></summary><label className="sr-only" htmlFor="dataset-context">Dataset context</label><textarea id="dataset-context" value={datasetContext} onChange={event => setDatasetContext(event.target.value)} maxLength={4000} rows={4} placeholder="Units or definitions, if needed" /><p>Included with new questions.</p></details>
      <div className="menu-questions"><div className="menu-section-title">Questions</div><button type="button" className={`new-question-link ${view.kind === 'new' ? 'active' : ''}`} onClick={() => { setDraft(''); setView({ kind: 'new' }) }}>New question</button>
        {questions.length ? <div className="question-archive">{questions.map(item => <button type="button" key={item.id} className={view.kind === 'question' && view.id === item.id ? 'active' : ''} aria-current={view.kind === 'question' && view.id === item.id ? 'page' : undefined} onClick={() => { if (item.table !== state.activeTable) state.selectTable(item.table); setView({ kind: 'question', id: item.id }) }}><span className="archive-title">{item.question}</span><small>{item.tableLabel} · {item.status === 'working' ? 'Working' : item.status === 'failed' ? 'Failed' : 'Answered'}</small></button>)}</div> : <p className="archive-empty">Past questions will appear here.</p>}</div>
    </aside>
    <div className="question-main">
      {state.error && <p className="error-message" role="alert">{state.error}</p>}
      {view.kind === 'dataset' && <div className="dataset-page"><DatasetOverview table={table} /><DataPreview table={table} preview={state.preview?.table === table.name ? state.preview : null} offset={state.offset} loading={state.previewLoading} onOffset={state.changeOffset} /><SchemaExplorer table={table} /></div>}
      {view.kind === 'new' && <QuestionPage table={table} draft={draft} onDraft={setDraft} onAsk={ask} busy={running} />}
      {view.kind === 'question' && selected && <QuestionPage table={table} record={selected} />}
    </div>
  </div>
}
