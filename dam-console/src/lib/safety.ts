export type ComponentKind = 'baseline' | 'scene' | 'task' | 'mode'
export interface SafetyNode {
  callback: string | null
  params: Record<string, unknown>
  [key: string]: unknown
}
export interface SafetyContainer {
  layer: string
  type: string
  nodes: SafetyNode[]
  loop?: boolean
}
export interface SafetyComponent {
  name: string
  kind: ComponentKind
  boundaries: Record<string, SafetyContainer>
}
export interface SafetyContract {
  observation_keys: string[]
  frame?: string
  action_units?: string[]
  joint_names?: string[]
}
export interface SafetyPlan {
  name: string
  base_yaml: string
  task: string
  components: SafetyComponent[]
  contract?: SafetyContract
}
export interface CompileResult {
  yaml: string
  task: string
  valid: boolean
  sources: Record<string, { component: string; kind: ComponentKind | 'base' }>
  diagnostics: { boundary: string; status: 'declaration-only' | 'inactive' | 'missing' | 'error'; message: string; code: string }[]
  runtime_verified: false
}
export interface DraftResult {
  components: SafetyComponent[]
  unresolved: string[]
  explanation: string
  runtime_verified: false
}
export interface SwitchPreview {
  token: string
  mode: 'hot_reload' | 'restart_required' | 'blocked' | 'unchanged'
  reasons: string[]
  changes: { path: string; before: unknown; after: unknown }[]
  current_version: number | null
  candidate_hash: string
}
export interface SwitchRecord {
  id: string
  status: 'pending' | 'applied' | 'saved_restart_required' | 'rejected'
  message: string
  persisted?: boolean
}
export interface SwitchStatus {
  active_hash: string | null
  saved_hash?: string | null
  active_error?: string | null
  history_error?: string | null
  saved_error?: string | null
  active_task?: string | null
  config_version: number | null
  pending: SwitchRecord | null
  history: SwitchRecord[]
}
const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080'
async function request<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`${BASE}/api/safety${path}`, {
    method: body === undefined ? 'GET' : method,
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json()
  if (!response.ok) {
    const detail = data.detail
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail ?? data))
  }
  return data as T
}
export const safetyApi = {
  list: () => request<{ plans: { name: string }[] }>('/plans'),
  load: (name: string) => request<SafetyPlan>(`/plans/${encodeURIComponent(name)}`),
  save: (plan: SafetyPlan) => request<{ name: string; created: boolean }>(`/plans/${encodeURIComponent(plan.name)}`, plan, 'PUT'),
  compile: (plan: SafetyPlan) => request<CompileResult>('/compile', plan),
  draft: (text: string, frame?: string) => request<DraftResult>('/draft', { text, ...(frame ? { frame } : {}) }),
  preview: (yaml: string, task: string, contract?: SafetyContract) => request<SwitchPreview>('/switch/preview', { yaml, task, ...(contract ? { contract } : {}) }),
  rollbackPreview: (id: string, contract?: SafetyContract) => request<SwitchPreview>('/switch/rollback/preview', { id, ...(contract ? { contract } : {}) }),
  apply: (token: string) => request<SwitchRecord>('/switch/apply', { token }),
  status: () => request<SwitchStatus>('/switch/status'),
}
export const safetyInput = 'rounded border border-dam-border bg-dam-bg px-3 py-2 text-sm text-dam-text w-full'
export const safetyButton = 'rounded border border-dam-border px-3 py-2 text-xs hover:bg-white/5 disabled:opacity-40 disabled:cursor-not-allowed'
