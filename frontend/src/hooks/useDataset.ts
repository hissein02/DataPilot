import { useCallback, useEffect, useState } from 'react'
import { deleteDataset, getDataset, getPreview, getSampleFile, uploadDataset } from '../lib/api'
import type { Dataset, Preview } from '../types/dataset'

function clearSavedWorkspace(workspaceId: string) {
  sessionStorage.removeItem(`datapilot.questions.${workspaceId}`)
  sessionStorage.removeItem(`datapilot.context.${workspaceId}`)
}

export function useDataset() {
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [activeTable, setActiveTable] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const workspaceId = sessionStorage.getItem('datapilot.workspace')
    if (!workspaceId) return
    let current = true
    getDataset(workspaceId)
      .then(restored => { if (current) { setDataset(restored); setActiveTable(restored.active_table) } })
      .catch(() => { if (current) { sessionStorage.removeItem('datapilot.workspace'); clearSavedWorkspace(workspaceId) } })
    return () => { current = false }
  }, [])

  const upload = useCallback(async (file: File) => {
    setError('')
    if (!/\.(csv|xlsx)$/i.test(file.name)) { setError('Choose a CSV or XLSX file.'); return }
    if (file.size > 25 * 1024 * 1024) { setError('Choose a file smaller than 25 MB.'); return }
    setLoading(true)
    try {
      const next = await uploadDataset(file)
      if (dataset) { void deleteDataset(dataset.workspace_id); clearSavedWorkspace(dataset.workspace_id) }
      setDataset(next)
      sessionStorage.setItem('datapilot.workspace', next.workspace_id)
      setActiveTable(next.active_table)
      setOffset(0)
      setPreview(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed. Try again.')
    } finally { setLoading(false) }
  }, [dataset])

  const loadSample = useCallback(async () => {
    setError('')
    try { await upload(await getSampleFile()) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load the sample dataset.') }
  }, [upload])

  useEffect(() => {
    if (!dataset || !activeTable) return
    let current = true
    setPreviewLoading(true)
    setError('')
    getPreview(dataset.workspace_id, activeTable, offset, 10)
      .then(result => { if (current) setPreview(result) })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : 'Could not load the preview.') })
      .finally(() => { if (current) setPreviewLoading(false) })
    return () => { current = false }
  }, [dataset, activeTable, offset])

  const selectTable = (name: string) => { setActiveTable(name); setOffset(0); setPreview(null) }
  const changeOffset = (value: number) => { setOffset(value); setPreview(null) }
  const clear = () => {
    if (dataset) { void deleteDataset(dataset.workspace_id); clearSavedWorkspace(dataset.workspace_id) }
    sessionStorage.removeItem('datapilot.workspace')
    setDataset(null); setActiveTable(''); setPreview(null); setOffset(0); setError('')
  }
  return { dataset, activeTable, preview, offset, loading, previewLoading, error, upload, loadSample, selectTable, changeOffset, clear }
}
