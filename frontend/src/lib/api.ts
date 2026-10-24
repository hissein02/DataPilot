import { analysisEventSchema, datasetSchema, previewSchema } from '../types/dataset'

async function check(response: Response): Promise<unknown> {
  const data: unknown = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = typeof data === 'object' && data !== null && 'error' in data && typeof data.error === 'string'
      ? data.error : typeof data === 'object' && data !== null && 'detail' in data && typeof data.detail === 'string'
        ? data.detail : `Request failed (${response.status}).`
    throw new Error(message)
  }
  return data
}

export async function uploadDataset(file: File) {
  const form = new FormData()
  form.append('file', file)
  return datasetSchema.parse(await check(await fetch('/api/datasets', { method: 'POST', body: form })))
}

export async function getDataset(workspaceId: string) {
  return datasetSchema.parse(await check(await fetch(`/api/datasets/${workspaceId}`)))
}

export async function getPreview(workspaceId: string, table: string, offset: number, limit: number) {
  const query = new URLSearchParams({ table, offset: String(offset), limit: String(limit) })
  return previewSchema.parse(await check(await fetch(`/api/datasets/${workspaceId}/preview?${query}`)))
}

export async function* analyzeDatasetStream(workspaceId: string, table: string, question: string, datasetContext: string, signal: AbortSignal) {
  const response = await fetch('/api/analysis/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: workspaceId, table, question, dataset_context: datasetContext }),
    signal,
  })
  if (!response.ok) await check(response)
  if (!response.body) throw new Error('The analysis stream is unavailable.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (data) yield analysisEventSchema.parse(JSON.parse(data))
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }
  } finally {
    reader.releaseLock()
  }
}

export async function deleteDataset(workspaceId: string) {
  await fetch(`/api/datasets/${workspaceId}`, { method: 'DELETE' })
}

export async function getSampleFile() {
  const response = await fetch('/sample/orders.csv')
  if (!response.ok) throw new Error('Could not load the sample dataset.')
  return new File([await response.blob()], 'orders.csv', { type: 'text/csv' })
}
