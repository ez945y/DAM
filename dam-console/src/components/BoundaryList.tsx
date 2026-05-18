'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import type { BoundaryConfig } from '@/lib/types'
import { Plus, Pencil, Trash2, RotateCcw } from 'lucide-react'

type BoundaryNode = BoundaryConfig['nodes'][number]

function nodeParams(node?: BoundaryNode): Record<string, unknown> {
  return node?.params ?? (node?.constraint?.params as Record<string, unknown> | undefined) ?? {}
}

function nodeCallback(node?: BoundaryNode): string | null {
  return node?.callback ?? (node?.constraint?.callback as string | undefined) ?? null
}

function numberParam(params: Record<string, unknown>, key: string, fallback: number): number {
  const value = params[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function boolParam(params: Record<string, unknown>, key: string, fallback: boolean): boolean {
  return typeof params[key] === 'boolean' ? params[key] : fallback
}

function withHardwareDefaults(params: Record<string, unknown>): Record<string, unknown> {
  return {
    max_staleness_ms: 1000,
    monitor_temperature: true,
    max_temperature_c: 80,
    monitor_current: true,
    max_current_a: 3,
    monitor_voltage: true,
    min_voltage_v: 10,
    max_voltage_v: 13,
    consecutive_fault_frames: 3,
    peak_action: 'warn',
    ...params,
  }
}

function HardwareWatchdogEditor({
  params,
  setParam,
}: {
  params: Record<string, unknown>
  setParam: (key: string, value: unknown) => void
}) {
  const metricRows = [
    { key: 'temperature', label: 'Temperature', enabled: 'monitor_temperature', threshold: 'max_temperature_c', unit: '°C', fallback: 80 },
    { key: 'current', label: 'Current', enabled: 'monitor_current', threshold: 'max_current_a', unit: 'A', fallback: 3 },
    { key: 'voltage_min', label: 'Min voltage', enabled: 'monitor_voltage', threshold: 'min_voltage_v', unit: 'V', fallback: 10 },
    { key: 'voltage_max', label: 'Max voltage', enabled: 'monitor_voltage', threshold: 'max_voltage_v', unit: 'V', fallback: 13 },
  ]

  return (
    <div className="rounded-lg border border-dam-border bg-dam-surface-2/50 p-3 space-y-3">
      <div className="grid grid-cols-[1fr_90px_52px] gap-2 text-[10px] uppercase tracking-wider text-dam-muted">
        <span>Metric</span>
        <span>Threshold</span>
        <span>On</span>
      </div>
      {metricRows.map(row => (
        <div key={row.key} className="grid grid-cols-[1fr_90px_52px] gap-2 items-center">
          <span className="text-xs text-dam-text">{row.label}</span>
          <label className="flex items-center gap-1 bg-dam-surface border border-dam-border rounded px-2 py-1">
            <input
              aria-label={`${row.label} threshold`}
              type="number"
              step="0.1"
              value={numberParam(params, row.threshold, row.fallback)}
              onChange={e => setParam(row.threshold, Number(e.target.value))}
              className="w-full bg-transparent text-dam-text text-xs font-mono outline-none"
            />
            <span className="text-[10px] text-dam-muted">{row.unit}</span>
          </label>
          <input
            aria-label={`Enable ${row.label}`}
            type="checkbox"
            checked={boolParam(params, row.enabled, true)}
            onChange={e => setParam(row.enabled, e.target.checked)}
            className="h-4 w-4 accent-dam-blue justify-self-center"
          />
        </div>
      ))}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-2 border-t border-dam-border/50">
        <label className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-dam-muted">Heartbeat</span>
          <div className="flex items-center gap-1 bg-dam-surface border border-dam-border rounded px-2 py-1">
            <input
              type="number"
              value={numberParam(params, 'max_staleness_ms', 1000)}
              onChange={e => setParam('max_staleness_ms', Number(e.target.value))}
              className="w-full bg-transparent text-dam-text text-xs font-mono outline-none"
            />
            <span className="text-[10px] text-dam-muted">ms</span>
          </div>
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-dam-muted">Stop After</span>
          <div className="flex items-center gap-1 bg-dam-surface border border-dam-border rounded px-2 py-1">
            <input
              type="number"
              min={1}
              value={numberParam(params, 'consecutive_fault_frames', 3)}
              onChange={e => setParam('consecutive_fault_frames', Math.max(1, Number(e.target.value)))}
              className="w-full bg-transparent text-dam-text text-xs font-mono outline-none"
            />
            <span className="text-[10px] text-dam-muted">frames</span>
          </div>
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-dam-muted">Peak Action</span>
          <select
            value={String(params.peak_action ?? 'warn')}
            onChange={e => setParam('peak_action', e.target.value)}
            className="w-full bg-dam-surface border border-dam-border rounded px-2 py-1 text-dam-text text-xs"
          >
            <option value="warn">warn</option>
            <option value="log">log</option>
            <option value="record">record</option>
          </select>
        </label>
      </div>
      <p className="text-[10px] text-dam-muted/80 leading-relaxed">
        Peaks are recorded as hardware_peak metadata. The robot stops only after the configured consecutive frames; heartbeat loss and hardware error codes still stop immediately.
      </p>
    </div>
  )
}

function BoundaryModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: BoundaryConfig
  onSave: (c: BoundaryConfig) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [type, setType] = useState<'single' | 'list' | 'graph'>(initial?.type ?? 'single')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const firstNode = initial?.nodes?.[0]
  const [node, setNode] = useState<BoundaryNode>(
    firstNode ?? { node_id: 'default', callback: null, fallback: 'emergency_stop', timeout_sec: 1, params: {} }
  )
  const params = nodeParams(node)
  const callback = nodeCallback(node)
  const isHardwareWatchdog = name === 'hardware_watchdog' || callback === 'hardware_watchdog'

  const setParam = (key: string, value: unknown) => {
    setNode(curr => ({ ...curr, params: { ...nodeParams(curr), [key]: value } }))
  }

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return }
    setSaving(true)
    try {
      const savedNode = isHardwareWatchdog
        ? { ...node, callback: callback ?? 'hardware_watchdog', params: withHardwareDefaults(params) }
        : node
      await onSave({ ...(initial ?? {}), name: name.trim(), type, nodes: [savedNode] })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <dialog open className="fixed inset-0 m-0 p-0 max-w-none w-full h-full border-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-dam-surface border border-dam-border rounded-xl p-6 w-full max-w-md space-y-4">
        <h3 className="text-dam-text font-bold">{initial ? 'Edit Boundary' : 'New Boundary'}</h3>
        <div className="space-y-3">
          <div>
            <label htmlFor="boundary-name" className="text-dam-muted text-xs uppercase tracking-wider block mb-1">Name</label>
            <input
              id="boundary-name"
              value={name}
              onChange={e => setName(e.target.value)}
              disabled={!!initial}
              placeholder="e.g. workspace"
              className="w-full bg-dam-surface-2 border border-dam-border rounded px-3 py-1.5 text-dam-text text-sm disabled:opacity-50"
            />
          </div>
          <div>
            <label htmlFor="boundary-type" className="text-dam-muted text-xs uppercase tracking-wider block mb-1">Type</label>
            <select
              id="boundary-type"
              value={type}
              onChange={e => setType(e.target.value as 'single' | 'list' | 'graph')}
              className="w-full bg-dam-surface-2 border border-dam-border rounded px-3 py-1.5 text-dam-text text-sm"
            >
              <option value="single">single</option>
              <option value="list">list</option>
              <option value="graph">graph</option>
            </select>
          </div>
          {isHardwareWatchdog && (
            <HardwareWatchdogEditor params={params} setParam={setParam} />
          )}
        </div>
        {error && <p className="text-dam-red text-xs">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-dam-muted border border-dam-border rounded hover:text-dam-text transition-colors">
            Cancel
          </button>
          <button
            onClick={() => { handleSave() }}
            disabled={saving}
            className="px-4 py-1.5 text-xs font-bold bg-dam-blue text-white rounded hover:bg-dam-blue-bright disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </dialog>
  )
}

export function BoundaryList() {
  const [boundaries, setBoundaries] = useState<BoundaryConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<BoundaryConfig | undefined>()

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listBoundaries()
      setBoundaries(res.boundaries)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const handleCreate = async (c: BoundaryConfig) => {
    await api.createBoundary(c)
    await load()
  }

  const handleUpdate = async (c: BoundaryConfig) => {
    await api.updateBoundary(c.name, c)
    await load()
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete boundary "${name}"?`)) return
    try {
      await api.deleteBoundary(name)
      await load()
    } catch { /* ignore */ }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-dam-muted text-xs">{boundaries.length} boundary config{boundaries.length !== 1 ? 's' : ''}</p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void load()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold border border-dam-border text-dam-muted rounded hover:text-dam-text transition-colors"
          >
            <RotateCcw size={12} /> Refresh
          </button>
          <button
            onClick={() => { setEditing(undefined); setShowModal(true) }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-dam-blue text-white rounded hover:bg-dam-blue-bright transition-colors"
          >
            <Plus size={12} /> New Boundary
          </button>
        </div>
      </div>

      {error ? (
        <div className="bg-dam-red/10 border border-dam-red/30 rounded-lg p-3 text-dam-red text-xs">
          Failed to load boundaries: {error}
        </div>
      ) : loading ? (
        <div className="text-dam-muted text-sm text-center py-8">Loading…</div>
      ) : boundaries.length === 0 ? (
        <div className="bg-dam-surface-2 border border-dashed border-dam-border rounded-xl p-8 text-center">
          <p className="text-dam-muted text-sm">No boundary configs yet.</p>
          <p className="text-dam-muted text-xs mt-1">Click &quot;New Boundary&quot; to create one.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {boundaries.map(b => (
            <div key={b.name} className="flex items-start gap-3 bg-dam-surface-2 border border-dam-border rounded-lg px-4 py-3 hover:border-dam-blue/50 transition-colors">
              <div className="flex-1 min-w-0">
                <p className="text-dam-text font-mono font-semibold text-sm">{b.name}</p>
                <p className="text-dam-muted text-xs">
                  {b.layer ?? 'L?'} · {b.type} · {b.nodes?.length ?? 0} node{(b.nodes?.length ?? 0) !== 1 ? 's' : ''}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(b.nodes ?? []).map((n, i) => {
                    const p = nodeParams(n)
                    const entries = Object.entries(p).slice(0, 6)
                    return (
                      <div key={n.node_id ?? i} className="text-[10px] text-dam-muted border border-dam-border/60 rounded px-2 py-1">
                        <span className="text-dam-text/80">{nodeCallback(n) ?? 'native'}</span>
                        {n.timeout_sec != null && <span> · {n.timeout_sec}s</span>}
                        {entries.length > 0 && (
                          <span> · {entries.map(([k, v]) => `${k}=${String(v)}`).join(' · ')}</span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
              <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-dam-blue-dim text-dam-blue border border-blue-800 uppercase">
                {b.type}
              </span>
              <button
                onClick={() => { setEditing(b); setShowModal(true) }}
                className="p-1.5 text-dam-muted hover:text-dam-blue transition-colors rounded"
                title="Edit"
              >
                <Pencil size={13} />
              </button>
              <button
                onClick={() => void handleDelete(b.name)}
                className="p-1.5 text-dam-muted hover:text-dam-red transition-colors rounded"
                title="Delete"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <BoundaryModal
          initial={editing}
          onSave={editing ? handleUpdate : handleCreate}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  )
}
