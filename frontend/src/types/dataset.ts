import { z } from 'zod'

const cell = z.union([z.string(), z.number(), z.boolean(), z.null()])
export const columnSchema = z.object({
  name: z.string(), display_name: z.string(), type: z.enum(['text', 'integer', 'number', 'date']),
  null_count: z.number(), null_percentage: z.number(), distinct_count: z.number(),
  distinct_method: z.string(), sample_values: z.array(cell), minimum: cell.optional(),
  maximum: cell.optional(), mean: cell.optional(), earliest: cell.optional(), latest: cell.optional(),
})
export const tableSchema = z.object({
  name: z.string(), display_name: z.string(), row_count: z.number(), column_count: z.number(),
  duplicate_rows: z.number(), columns: z.array(columnSchema), warnings: z.array(z.string()),
})
export const datasetSchema = z.object({
  workspace_id: z.string(), filename: z.string(), tables: z.array(tableSchema),
  active_table: z.string(), upload_duration_ms: z.number(),
})
export const previewSchema = z.object({
  table: z.string(), offset: z.number(), limit: z.number(), total_rows: z.number(),
  rows: z.array(z.record(z.string(), cell)),
})
export const analysisSchema = z.object({
  answer: z.string(), limitation: z.string().nullable(), table: z.string(),
  sql: z.string().nullable(), columns: z.array(z.string()),
  rows: z.array(z.record(z.string(), cell)), truncated: z.boolean(),
  execution_duration_ms: z.number(), total_duration_ms: z.number(),
  plan: z.array(z.string()).default([]),
  queries: z.array(z.object({
    purpose: z.string(), sql: z.string(), columns: z.array(z.string()),
    rows: z.array(z.record(z.string(), cell)), truncated: z.boolean(),
    execution_duration_ms: z.number(),
  })).default([]),
  trace: z.array(z.object({ step: z.string(), status: z.string(), message: z.string() })).default([]),
  execution_count: z.number().default(0),
})
export const analysisEventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('progress'), step: z.string(), status: z.string(), message: z.string() }),
  z.object({ type: z.literal('result'), report: analysisSchema }),
  z.object({ type: z.literal('error'), message: z.string() }),
  z.object({ type: z.literal('done') }),
])
export type Dataset = z.infer<typeof datasetSchema>
export type DatasetTable = z.infer<typeof tableSchema>
export type Preview = z.infer<typeof previewSchema>
export type Analysis = z.infer<typeof analysisSchema>
export type AnalysisEvent = z.infer<typeof analysisEventSchema>
