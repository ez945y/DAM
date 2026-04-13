const API_BASE =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080')
    : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080')

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

import type { BoundaryConfig, RiskEvent, RiskLogStats, RuntimeStatus, UsbDeviceInfo } from './types'

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
    limit?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.since != null) q.set('since', String(params.since))
    if (params?.until != null) q.set('until', String(params.until))
    if (params?.min_risk_level) q.set('min_risk_level', params.min_risk_level)
    if (params?.rejected_only) q.set('rejected_only', 'true')
    if (params?.clamped_only) q.set('clamped_only', 'true')
    if (params?.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return apiFetch<{ events: RiskEvent[]; count: number }>(`/risk-log${qs ? `?${qs}` : ''}`)
  },
  getRiskLogStats: () => apiFetch<RiskLogStats>('/risk-log/stats'),
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
    apiFetch<{ fallbacks: { name: string; description?: string; escalates_to?: string }[] }>(
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
  recheckHardware: () => apiFetch<{ success: boolean; state: string }>('/control/recheck-hardware', { method: 'POST' }),

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
