import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

function streamResponse(events: object[]) {
  const text = events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')
  return { ok: true, body: new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(text)); controller.close() } }) }
}

describe('App', () => {
  it('introduces the analytical workspace', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Open a dataset.' })).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('DataPilot')
  })

  it('rejects unsupported file extensions before upload', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.change(screen.getByLabelText('Choose dataset'), { target: { files: [new File(['x'], 'notes.txt')] } })
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a CSV or XLSX file.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows uploaded metadata and the paginated preview', async () => {
    const dataset = {
      workspace_id: 'a'.repeat(32), filename: 'orders.csv', active_table: 'orders', upload_duration_ms: 12,
      tables: [{ name: 'orders', display_name: 'Orders', row_count: 12, column_count: 2, duplicate_rows: 0, warnings: [],
        columns: [
          { name: 'item', display_name: 'Item', type: 'text', null_count: 0, null_percentage: 0, distinct_count: 2, distinct_method: 'exact', sample_values: ['Pen', 'Book'] },
          { name: 'quantity', display_name: 'Quantity', type: 'integer', null_count: 0, null_percentage: 0, distinct_count: 2, distinct_method: 'exact', sample_values: [2, 3] },
        ] }],
    }
    const previewRows = Array.from({ length: 12 }, (_, index) => ({ item: index === 1 ? 'Book' : 'Pen', quantity: index + 1 }))
    const fetchMock = vi.fn(async (input: string) => {
      if (input === '/api/datasets') return { ok: true, json: async () => dataset }
      const query = new URLSearchParams(input.split('?')[1])
      const offset = Number(query.get('offset'))
      const limit = Number(query.get('limit'))
      return { ok: true, json: async () => ({ table: 'orders', offset, limit, total_rows: 12, rows: previewRows.slice(offset, offset + limit) }) }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.change(screen.getByLabelText('Choose dataset'), { target: { files: [new File(['item,quantity\nPen,2'], 'orders.csv', { type: 'text/csv' })] } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dataset' })).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Orders' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('table')).toHaveTextContent('Book'))
    expect(screen.getByRole('table')).toHaveTextContent('Quantity')
    expect(screen.getAllByRole('row')).toHaveLength(11)
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('limit=10'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByText('11–12 of 12')).toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(3))
    expect(screen.queryByRole('heading', { name: 'Visualizations' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Columns' })).toBeInTheDocument()
    const columnDetails = screen.getByRole('region', { name: 'Column details' })
    expect(columnDetails).toHaveAttribute('tabindex', '0')
    expect(within(columnDetails).queryByText('Pen · Book')).not.toBeInTheDocument()
    expect(screen.queryByText(/03 \/ STRUCTURE/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Dataset context', { selector: 'summary' }))
    expect(screen.getByRole('textbox', { name: /Dataset context/ })).toBeInTheDocument()
  })

  it('shows upload errors and leaves the loading state', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ error: 'Malformed CSV.' }) })))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Choose dataset'), { target: { files: [new File(['bad'], 'bad.csv')] } })
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Malformed CSV.'))
    expect(screen.getByRole('button', { name: 'Choose file' })).toBeEnabled()
  })

  it('asks a question and shows the answer, rows, and SQL', async () => {
    const dataset = {
      workspace_id: 'a'.repeat(32), filename: 'orders.csv', active_table: 'orders', upload_duration_ms: 12,
      tables: [{ name: 'orders', display_name: 'Orders', row_count: 2, column_count: 1, duplicate_rows: 0, warnings: [],
        columns: [{ name: 'quantity', display_name: 'Quantity', type: 'integer', null_count: 0, null_percentage: 0, distinct_count: 2, distinct_method: 'exact', sample_values: [2, 3] }],
      }],
    }
    const sent: Record<string, unknown>[] = []
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === '/api/datasets') return { ok: true, json: async () => dataset }
      if (input === `/api/datasets/${dataset.workspace_id}`) return { ok: true, json: async () => dataset }
      if (input === '/api/analysis/stream') {
        sent.push(JSON.parse(String(init?.body)))
        return streamResponse([
          { type: 'progress', step: 'plan', status: 'complete', message: 'Check the total quantity.' },
          { type: 'progress', step: 'validate_sql', status: 'retry', message: 'Function UNSAFE is not allowed.' },
          { type: 'result', report: { answer: 'Total quantity: 5.', limitation: null, table: 'orders', sql: 'SELECT SUM(quantity) AS total FROM orders', columns: ['total'], rows: [{ total: 5 }], truncated: false, execution_duration_ms: 3, total_duration_ms: 50, plan: ['Check the total quantity'], queries: [{ purpose: 'Check the total quantity', sql: 'SELECT SUM(quantity) AS total FROM orders', columns: ['total'], rows: [{ total: 5 }], truncated: false, execution_duration_ms: 3 }], trace: [{ step: 'plan', status: 'complete', message: 'Check the total quantity.' }, { step: 'validate_sql', status: 'retry', message: 'Function UNSAFE is not allowed.' }], execution_count: 1 } },
          { type: 'done' },
        ])
      }
      return { ok: true, json: async () => ({ table: 'orders', offset: 0, limit: 20, total_rows: 2, rows: [{ quantity: 2 }, { quantity: 3 }] }) }
    })
    vi.stubGlobal('fetch', fetchMock)
    const page = render(<App />)
    fireEvent.change(screen.getByLabelText('Choose dataset'), { target: { files: [new File(['quantity\n2\n3'], 'orders.csv')] } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'New question' })).toBeInTheDocument())
    fireEvent.click(screen.getByText('Dataset context', { selector: 'summary' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Dataset context/ }), { target: { value: 'Amounts are in euros.' } })
    fireEvent.click(screen.getByRole('button', { name: 'New question' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Question' }), { target: { value: 'What is the total quantity?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask question' }))
    await waitFor(() => expect(screen.getByText('Total quantity: 5.')).toBeInTheDocument())
    expect(screen.queryByRole('textbox', { name: 'Question' })).not.toBeInTheDocument()
    const answer = screen.getByRole('region', { name: 'Answer' })
    const supporting = screen.getByRole('region', { name: 'Supporting results' })
    const process = screen.getByRole('region', { name: 'Analysis process' })
    expect(within(answer).getByText('Total quantity: 5.')).toBeInTheDocument()
    expect(within(supporting).getByText('Check the total quantity')).toBeInTheDocument()
    expect(within(answer).queryByText('Function UNSAFE is not allowed.')).not.toBeInTheDocument()
    expect(within(supporting).queryByText('Function UNSAFE is not allowed.')).not.toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('DataPilot')
    fireEvent.click(within(process).getByText(/Analysis process/))
    expect(screen.getByText('Function UNSAFE is not allowed.')).toBeVisible()
    fireEvent.click(within(supporting).getByText('Check the total quantity'))
    fireEvent.click(screen.getByText('View executed SQL'))
    expect(screen.getByText('SELECT SUM(quantity) AS total FROM orders')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Dataset' }))
    fireEvent.click(screen.getByRole('button', { name: /What is the total quantity\?/ }))
    expect(screen.getByText('Total quantity: 5.')).toBeVisible()
    fireEvent.click(screen.getAllByRole('button', { name: 'New question' })[0])
    expect(screen.getByRole('textbox', { name: 'Question' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: /Dataset context/ }), { target: { value: 'Revised context.' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Question' }), { target: { value: 'How many records?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask question' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /How many records\?/ })).toBeInTheDocument())
    expect(sent).toHaveLength(2)
    expect(sent[0]).toMatchObject({ workspace_id: dataset.workspace_id, table: 'orders', question: 'What is the total quantity?', dataset_context: 'Amounts are in euros.' })
    expect(sent[1]).toMatchObject({ question: 'How many records?', dataset_context: 'Revised context.' })
    expect(sent[1]).not.toHaveProperty('history')
    fireEvent.click(screen.getByRole('button', { name: /What is the total quantity\?/ }))
    fireEvent.click(screen.getByText('Dataset context used'))
    expect(screen.getByText('Amounts are in euros.')).toBeVisible()
    page.unmount()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: /What is the total quantity\?/ })).toBeInTheDocument())
    fireEvent.click(screen.getByText('Dataset context', { selector: 'summary' }))
    expect(screen.getByRole('textbox', { name: /Dataset context/ })).toHaveValue('Revised context.')
    fireEvent.click(screen.getByRole('button', { name: /What is the total quantity\?/ }))
    expect(screen.getByText('Total quantity: 5.')).toBeVisible()
  })

  it('shows the missing API key error in the analysis panel', async () => {
    const dataset = { workspace_id: 'a'.repeat(32), filename: 'orders.csv', active_table: 'orders', upload_duration_ms: 12,
      tables: [{ name: 'orders', display_name: 'Orders', row_count: 1, column_count: 1, duplicate_rows: 0, warnings: [],
        columns: [{ name: 'quantity', display_name: 'Quantity', type: 'integer', null_count: 0, null_percentage: 0, distinct_count: 1, distinct_method: 'exact', sample_values: [2] }] }] }
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      if (input === '/api/datasets') return { ok: true, json: async () => dataset }
      if (input === '/api/analysis/stream') return streamResponse([{ type: 'error', message: 'Set OPENAI_API_KEY to enable analysis.' }, { type: 'done' }])
      return { ok: true, json: async () => ({ table: 'orders', offset: 0, limit: 20, total_rows: 1, rows: [{ quantity: 2 }] }) }
    }))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Choose dataset'), { target: { files: [new File(['quantity\n2'], 'orders.csv')] } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'New question' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'New question' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Question' }), { target: { value: 'How many rows are in this table?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask question' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Set OPENAI_API_KEY to enable analysis.'))
  })
})
