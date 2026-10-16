const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080'

export interface CandidateOutcome {
  status: 'evaluated' | 'unsupported' | 'error'
  decision: string | null
  output?: number[] | null
  output_velocities?: number[] | null
  correction_l2?: number | null
  guards?: { name: string; decision: string; reason: string }[]
  issues: string[]
}
export interface ComparisonCycle {
  cycle: number
  timestamp: number | null
  left: CandidateOutcome
  right: CandidateOutcome
  comparable: boolean
  changed: boolean
  output_delta_l2: number | null
}
export interface SafetyComparison {
  id: string
  status: 'complete' | 'partial' | 'error'
  mcap: string
  mcap_sha256: string
  total: number
  compared: number
  error_cycles: number
  difference_count: number
  distributions: { left: Record<string, number>; right: Record<string, number> }
  cycles: ComparisonCycle[]
  differences: ComparisonCycle[]
  top_differences: ComparisonCycle[]
  limitations: string[]
  evaluation_mode: string
  configs: Record<'left' | 'right', { name: string; yaml: string; sha256: string }>
}
export interface SafetyRegression {
  id: string
  name: string
  mcap: string
  mcap_sha256: string
  cycle_start: number
  cycle_end: number
  config: { name: string; yaml: string; sha256: string }
  expected: { cycle: number; decision: string }[]
}
export interface RegressionResult {
  id: string
  status: 'pass' | 'fail' | 'error'
  checked?: number
  mismatches: unknown[]
  issues: unknown[]
}
async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}/api/safety${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await response.text()
  let data: unknown
  try { data = JSON.parse(text) } catch {
    throw new Error(response.ok ? 'Server returned an invalid JSON response' : `HTTP ${response.status}: ${response.statusText || 'Request failed'}`)
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : undefined
    const explanation = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map(item => item?.msg ?? JSON.stringify(item)).join('; ')
      : detail == null ? response.statusText || 'Request failed' : JSON.stringify(detail)
    throw new Error(`HTTP ${response.status}: ${explanation}`)
  }
  return data as T
}
export const safetyComparisonApi = {
  compare: (mcap: string, left_stack: string, right_stack: string) =>
    call<SafetyComparison>('/comparisons', { mcap, left_stack, right_stack }),
  cases: () => call<{ cases: SafetyRegression[]; errors: unknown[] }>('/regressions'),
  save: (body: { comparison_id: string; name: string; side: 'left' | 'right'; cycle_start: number; cycle_end: number; expected_decision?: string }) =>
    call<SafetyRegression>('/regressions', body),
  run: (id: string) => call<RegressionResult>(`/regressions/${encodeURIComponent(id)}/run`, {}),
}
