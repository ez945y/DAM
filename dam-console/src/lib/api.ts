const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, {
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = (err as { detail?: unknown }).detail
    let message: string
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d)).join('; ')
    } else if (detail === null || detail === undefined) {
      message = `HTTP ${res.status}`
    } else {
      message = JSON.stringify(detail)
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

import type {
  BoundaryConfig,
  ExperimentDef,
  ExperimentResult,
  RiskEvent,
  RiskLogStats,
  RuntimeStatus,
  UsbDeviceInfo,
} from './types'

export const api = {
  // ── Telemetry ────────────────────────────────────────────────────────────
  getTelemetryHistory: (n = 50) =>
    apiFetch<{ events: CycleEventRaw[]; total: number }>(`/telemetry/history?n=${n}`),

  // ── Risk log ─────────────────────────────────────────────────────────────
  getRiskLog: (params?: {
    since?: number
    until?: number
    min_risk_level?: string
    rejected_only?: boolean
    clamped_only?: boolean
    failure_type?: string
    limit?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.since != null) q.set('since', String(params.since))
    if (params?.until != null) q.set('until', String(params.until))
    if (params?.min_risk_level) q.set('min_risk_level', params.min_risk_level)
    if (params?.rejected_only) q.set('rejected_only', 'true')
    if (params?.clamped_only) q.set('clamped_only', 'true')
    if (params?.failure_type) q.set('failure_type', params.failure_type)
    if (params?.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return apiFetch<{ events: RiskEvent[]; count: number }>(`/risk-log${qs ? `?${qs}` : ''}`)
  },
  getRiskLogStats: () => apiFetch<RiskLogStats>('/risk-log/stats'),
  clearRiskLog: () =>
    apiFetch<{ cleared: boolean }>('/risk-log/clear', { method: 'POST' }),
  exportRiskLogJsonUrl: () => `${API_BASE}/api/risk-log/export/json`,
  exportRiskLogCsvUrl: () => `${API_BASE}/api/risk-log/export/csv`,

  // ── Boundaries ────────────────────────────────────────────────────────────
  listBoundaries: () => apiFetch<{ boundaries: BoundaryConfig[] }>('/boundaries'),
  getBoundary: (name: string) => apiFetch<BoundaryConfig>(`/boundaries/${encodeURIComponent(name)}`),
  createBoundary: (config: BoundaryConfig) =>
    apiFetch<BoundaryConfig>('/boundaries', { method: 'POST', body: JSON.stringify(config) }),
  updateBoundary: (name: string, config: Partial<BoundaryConfig>) =>
    apiFetch<BoundaryConfig>(`/boundaries/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  deleteBoundary: (name: string) =>
    apiFetch<void>(`/boundaries/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // ── Runtime control ───────────────────────────────────────────────────────
  getStatus: () => apiFetch<RuntimeStatus & { has_rust?: boolean }>('/control/status'),
  getCallbacks: () =>
    apiFetch<{ callbacks: { name: string; layer: string; description?: string; params?: any }[] }>(
      '/control/callbacks'
    ),
  getCallbackCatalog: (grouped: boolean = false) =>
    apiFetch<{
      callbacks?: { name: string; layer: string; description?: string; params?: any }[],
      groups?: { layer: string; callbacks: any[] }[]
    }>(`/catalog/callbacks?grouped=${grouped}`),
  getGuardCatalog: () =>
    apiFetch<{ guards: { kind: string; layer: string; description: string; class_name: string }[] }>(
      '/catalog/guards'
    ),
  getFallbacks: () =>
    apiFetch<{ fallbacks: { name: string; type?: string; params?: Record<string, unknown>; description?: string; escalates_to?: string }[] }>(
      '/control/fallbacks'
    ),
  start: (params?: { task_name?: string; n_cycles?: number; cycle_budget_ms?: number }) => {
    const q = new URLSearchParams()
    if (params?.task_name) q.set('task_name', params.task_name)
    if (params?.n_cycles !== undefined) q.set('n_cycles', String(params.n_cycles))
    if (params?.cycle_budget_ms) q.set('cycle_budget_ms', String(params.cycle_budget_ms))
    return apiFetch<{ started: boolean; state: string }>(`/control/start?${q}`, { method: 'POST' })
  },
  pause: () => apiFetch<{ paused: boolean; state: string }>('/control/pause', { method: 'POST' }),
  resume: () => apiFetch<{ resumed: boolean; state: string }>('/control/resume', { method: 'POST' }),
  stop: () => apiFetch<{ stopped: boolean; state: string }>('/control/stop', { method: 'POST' }),
  emergencyStop: () => apiFetch<{ emergency_stop: boolean; state: string }>('/control/estop', { method: 'POST' }),
  reset: () => apiFetch<{ reset: boolean; state: string }>('/control/reset', { method: 'POST' }),
  confirmFault: () => apiFetch<{ success: boolean; backend_state: string }>('/control/confirm-fault', { method: 'POST' }),
  recheckHardware: () => apiFetch<{ success: boolean; state: string }>('/control/recheck-hardware', { method: 'POST' }),

  // ── Experiments ──────────────────────────────────────────────────────────
  listExperiments: () =>
    apiFetch<{ experiments: ExperimentDef[] }>('/experiments'),
  runExperiment: (id: string, params?: Record<string, unknown>) =>
    apiFetch<ExperimentResult>(`/experiments/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      body: JSON.stringify({ params: params ?? {} }),
    }),
  experimentArtifactUrl: (path: string) =>
    `${API_BASE}/api/experiments/artifact?path=${encodeURIComponent(path)}`,

  // ── MCAP Sessions ─────────────────────────────────────────────────────────
  listMcapSessions: () =>
    apiFetch<{ sessions: McapSessionSummary[] }>('/mcap/sessions'),
  getMcapSession: (filename: string) =>
    apiFetch<McapSessionDetail>(`/mcap/sessions/${encodeURIComponent(filename)}`),
  listMcapCycles: (filename: string, sinceCycleId?: number) =>
    apiFetch<{ filename: string; count: number; cycles: McapCycle[] }>(
      `/mcap/sessions/${encodeURIComponent(filename)}/cycles${sinceCycleId != null ? `?since_cycle_id=${sinceCycleId}` : ''}`
    ),
  getMcapCycleDetail: (filename: string, cycleId: number, tsNs?: number | null) =>
    apiFetch<McapCycleDetail>(
      `/mcap/sessions/${encodeURIComponent(filename)}/cycles/${cycleId}${tsNs ? `?ts_ns=${tsNs}` : ''}`
    ),
  findMcapSession: (cycleId: number) =>
    apiFetch<{ cycle_id: number; filename: string | null; found: boolean }>(
      `/mcap/find?cycle_id=${cycleId}`
    ),
  mcapLiveSession: () =>
    apiFetch<{ filename: string | null; active: boolean; updated_at?: number }>(
      '/mcap/live'
    ),
  deleteMcapSession: (filename: string) =>
    apiFetch<{ success: boolean }>(`/mcap/sessions/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  archiveMcapSessions: (filenames: string[]) =>
    apiFetch<{
      success: boolean
      archived: Array<{ filename: string; archived_filename: string; archived_path: string }>
      failed: string[]
    }>('/mcap/sessions/archive', {
      method: 'POST',
      body: JSON.stringify({ filenames }),
    }),
  listMcapFrames: (filename: string, cam: string) =>
    apiFetch<{ camera: string; count: number; frames: McapFrame[] }>(
      `/mcap/sessions/${encodeURIComponent(filename)}/frames/${encodeURIComponent(cam)}`
    ),
  mcapFrameUrl: (filename: string, cam: string, idx: number) =>
    `${API_BASE}/api/mcap/sessions/${encodeURIComponent(filename)}/frame/${encodeURIComponent(cam)}/${idx}`,
  mcapFrameAtUrl: (filename: string, cam: string, tsNs: number) =>
    `${API_BASE}/api/mcap/sessions/${encodeURIComponent(filename)}/frame_at/${encodeURIComponent(cam)}?ts_ns=${tsNs}`,
  mcapDownloadUrl: (filename: string) =>
    `${API_BASE}/api/mcap/sessions/${encodeURIComponent(filename)}/download`,

  // ── Stackfile library (data/stackfiles, separate from live) ───────────────
  listStackfiles: () =>
    apiFetch<{
      entries: { name: string; size_bytes: number; modified_at: number }[]
      live: { exists: boolean }
    }>('/stackfiles'),
  getStackfile: async (name: string): Promise<string> => {
    const res = await fetch(`${API_BASE}/api/stackfiles/${encodeURIComponent(name)}`, {
      cache: 'no-store',
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.text()
  },
  saveStackfile: (name: string, yaml: string) =>
    apiFetch<{ name: string; created: boolean }>(`/stackfiles/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ yaml }),
    }),
  renameStackfile: (name: string, newName: string) =>
    apiFetch<{ name: string }>(`/stackfiles/${encodeURIComponent(name)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ new_name: newName }),
    }),
  deleteStackfile: (name: string) =>
    apiFetch<void>(`/stackfiles/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  applyStackfile: (name: string) =>
    apiFetch<{ applied: string; live_path: string }>(
      `/stackfiles/${encodeURIComponent(name)}/apply`,
      { method: 'POST' },
    ),

  // ── Replay-through-guards jobs ────────────────────────────────────────────
  startReplayJob: (mcap: string, stack: string) =>
    apiFetch<{ job_id: string; mcap: string; stack: string }>('/replay/jobs', {
      method: 'POST',
      body: JSON.stringify({ mcap, stack }),
    }),
  replayEventsUrl: (jobId: string) =>
    `${API_BASE}/api/replay/jobs/${encodeURIComponent(jobId)}/events`,
  stopReplayJob: (jobId: string) =>
    apiFetch<{ stopping: string }>(`/replay/jobs/${encodeURIComponent(jobId)}/stop`, {
      method: 'POST',
    }),

  // ── System ────────────────────────────────────────────────────────────────
  saveConfig: (yaml: string) =>
    apiFetch<{ success: boolean }>('/system/save-config', {
      method: 'POST',
      body: JSON.stringify({ yaml }),
    }),
  restart: (adapter: string, yaml: string) =>
    apiFetch<{ restarting: boolean }>('/system/restart', {
      method: 'POST',
      body: JSON.stringify({ adapter, yaml }),
    }),
}

// ── USB scan ──────────────────────────────────────────────────────────────────

export async function scanUsbDevices(): Promise<{ devices: UsbDeviceInfo[]; count: number }> {
  const data = await apiFetch<{ devices: Omit<UsbDeviceInfo, 'selected'>[]; count: number }>('/system/usb-devices')
  return {
    ...data,
    devices: (data.devices ?? []).map((d) => ({ ...d, selected: false })),
  }
}

// Internal type for raw API response
interface CycleEventRaw {
  [key: string]: unknown
}

// ── MCAP types ────────────────────────────────────────────────────────────────

export interface McapSessionSummary {
  filename: string
  size_bytes: number
  size_mb: number
  created_at: number  // unix timestamp
}

export interface McapSessionDetail extends McapSessionSummary {
  metadata: Record<string, string>
  stats: {
    total_cycles: number
    violation_cycles: number
    clamp_cycles: number
    duration_sec: number
    cameras: string[]
    violated_layers: string[]
    clamped_layers: string[]
    failure_types?: Record<string, number>
  }
  error?: string
}

export interface McapFrame {
  idx: number
  log_time_ns: number
  timestamp: number
}

export interface McapCycle {
  cycle_id: number
  seq: number
  timestamp_ns: number
  timestamp: number
  has_violation: boolean
  has_clamp: boolean
  violated_layer_mask: number
  clamped_layer_mask: number
  violated_layers: string[]
  clamped_layers: string[]
  failure_type?: string | null
}

export interface McapCycleDetail {
  cycle_id: number
  timestamp_ns: number
  timestamp: number
  has_violation: boolean
  has_clamp: boolean
  violated_layer_mask: number
  clamped_layer_mask: number
  violated_layers: string[]
  clamped_layers: string[]
  failure_type?: string | null
  failure_guard_names?: string[]
  failure_layers?: string[]
  failure_decisions?: string[]
  failure_reasons?: string[]
  failure_tuple?: Record<string, unknown> | null
  active_task: string | null
  active_boundaries: string[]
  // Latency (from /dam/cycle quick access)
  source_ms: number
  policy_ms: number
  guards_ms: number
  sink_ms: number
  total_ms: number
  // Full latency breakdown (from /dam/latency)
  latency: Record<string, number>
  // Observation state
  observation: {
    joint_positions: number[]
    joint_velocities: number[] | null
    end_effector_pose: number[] | null
    force_torque: number[] | null
    obs_timestamp: number | null
    [key: string]: unknown
  } | null
  // Policy action
  action: {
    target_positions: number[]
    target_velocities: number[] | null
    validated_positions: number[] | null
    validated_velocities: number[] | null
    was_clamped: boolean
    fallback_triggered: string | null
    [key: string]: unknown
  } | null
  // Guard results (one per guard that executed)
  guard_results: Array<{
    guard_name: string
    layer: number
    layer_name: string
    decision: number
    decision_name: string
    reason: string
    latency_ms: number | null
    is_violation: boolean
    is_clamp: boolean
    fault_source: string | null
    metadata?: Record<string, unknown>
  }>
}
