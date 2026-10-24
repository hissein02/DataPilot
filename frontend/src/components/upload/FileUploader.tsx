import { useRef, useState } from 'react'

type Props = { onFile: (file: File) => void; onSample: () => void; loading: boolean; error: string }

export function FileUploader({ onFile, onSample, loading, error }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  return <section className="upload-screen" aria-labelledby="upload-title">
    <div className="upload-copy"><h1 id="upload-title">Open a dataset.</h1><p>CSV or XLSX, up to 25 MB.</p></div>
    <div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files[0]) onFile(event.dataTransfer.files[0]) }}>
      <h2>Drop a file here</h2>
      <input ref={input} type="file" accept=".csv,.xlsx" className="sr-only" aria-label="Choose dataset" onChange={event => { const file = event.target.files?.[0]; if (file) onFile(file); event.target.value = '' }} />
      <button className="primary-button" type="button" disabled={loading} onClick={() => input.current?.click()}>{loading ? 'Processing…' : 'Choose file'}</button>
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    <button className="sample-link" type="button" disabled={loading} onClick={onSample}>Try sample data</button>
  </section>
}
