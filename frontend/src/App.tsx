import { DatasetWorkspace } from './components/workspace/DatasetWorkspace'
import { FileUploader } from './components/upload/FileUploader'
import { useDataset } from './hooks/useDataset'

export default function App() {
  const state = useDataset()
  const table = state.dataset?.tables.find(item => item.name === state.activeTable)

  return <div className="shell">
    <header className="header">
      <div className="brand">DataPilot</div>
      {state.dataset && <button type="button" className="replace-button" onClick={state.clear}>Replace dataset</button>}
    </header>
    <main className="site-content">
      {!state.dataset ? <FileUploader onFile={state.upload} onSample={state.loadSample} loading={state.loading} error={state.error} /> : table && <DatasetWorkspace key={state.dataset.workspace_id} state={state} table={table} />}
    </main>
    <footer className="site-footer">DataPilot</footer>
  </div>
}
