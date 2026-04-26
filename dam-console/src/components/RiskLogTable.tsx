'use client'
import React, { useState, useEffect, useRef } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { RiskBadge } from '@/components/RiskBadge'
import type { PerfSnapshot, GuardStatus, RiskEvent, RiskLevel, RiskLogStats } from '@/lib/types'
import { Download, ChevronRight, ChevronDown, Activity, Shield, Hash, Clock, Play, Pause } from 'lucide-react'

// ── Perf breakdown widget (pipeline + guard layers only) ──────────────────

const STAGE_COLORS: Record<string, string> = {
  source: '#6366F1', policy: '#F59E0B', guards: '#10B981', sink: '#3B82F6',
}
const STAGE_LABELS: Record<string, string> = {
  source: 'Source', policy: 'Policy', guards: 'Guards', sink: 'Sink',
}
const STAGE_ORDER = ['source', 'policy', 'guards', 'sink'] as const

const LAYER_META: Record<string, { label: string; color: string }> = {
  L0: { label: 'L0 OOD',       color: '#A78BFA' },
  L1: { label: 'L1 Preflight', color: '#34D399' },
  L2: { label: 'L2 Motion',    color: '#10B981' },
  L3: { label: 'L3 Execution', color: '#6EE7B7' },
  L4: { label: 'L4 Hardware',  color: '#F87171' },
}

/** Human-readable labels for raw latency_ms keys emitted by the runtime. */
const LATENCY_KEY_LABELS: Record<string, string> = {
  obs:      'Source (Read)',
  policy:   'Policy (Predict)',
  validate: 'Guards (Safety Checks)',
  sink:     'Sink (Dispatch)',
  total:    'Total',
  source:   'Source (Read)',
  guards:   'Guards (Safety Checks)',
}

function PerfDetail({ perf, totalMs }: { perf: PerfSnapshot; totalMs: number }) {
  const maxStage = Math.max(...STAGE_ORDER.map(s => perf.stages[s] ?? 0), 0.001)
  const layerKeys = Object.keys(perf.layers ?? {}).sort((a, b) => a.localeCompare(b))

  return (
    <div className="space-y-3">
      {/* Pipeline stages */}
      <div className="space-y-1.5">
        <p className="text-[9px] font-bold text-dam-orange uppercase tracking-widest">Pipeline Stages</p>
        {STAGE_ORDER.map(s => {
          const ms = perf.stages[s] ?? 0
          const pct = totalMs > 0 ? (ms / totalMs) * 100 : 0
          const barW = totalMs > 0 ? (ms / Math.max(totalMs, maxStage)) * 100 : 0
          return (
            <div key={s} className="flex items-center gap-2">
              <span className="w-14 text-[10px] text-dam-muted shrink-0">{STAGE_LABELS[s]}</span>
              <div className="flex-1 h-2 bg-dam-surface-1 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${barW}%`, background: STAGE_COLORS[s] }}
                />
              </div>
              <span className="w-16 text-right font-mono text-[10px] text-dam-text">
                {ms.toFixed(1)} ms
              </span>
              <span className="w-8 text-right font-mono text-[9px] text-dam-muted">
                {pct.toFixed(0)}%
              </span>
            </div>
          )
        })}
        <div className="flex items-center gap-2 border-t border-dam-border/30 pt-1 mt-1">
          <span className="w-14 text-[10px] font-bold text-dam-muted shrink-0">Total</span>
          <div className="flex-1" />
          <span className="w-16 text-right font-mono text-[10px] font-bold text-dam-orange">
            {totalMs.toFixed(1)} ms
          </span>
          <span className="w-8" />
        </div>
      </div>

      {/* Guard layers */}
      {layerKeys.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[9px] font-bold text-dam-orange uppercase tracking-widest">Guard Layers</p>
          {layerKeys.map(k => {
            const ms = perf.layers[k] ?? 0
            const barW = totalMs > 0 ? (ms / totalMs) * 100 : 0
            const meta = LAYER_META[k]
            return (
              <div key={k} className="flex items-center gap-2">
                <span className="w-14 text-[10px] text-dam-muted shrink-0">
                  {meta?.label ?? k}
                </span>
                <div className="flex-1 h-2 bg-dam-surface-1 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{ width: `${barW}%`, background: meta?.color ?? '#6B6B6B' }}
                  />
                </div>
                <span className="w-16 text-right font-mono text-[10px] text-dam-text">
                  {ms.toFixed(1)} ms
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Expandable guard result item ──────────────────────────────────────────

function GuardResultItem({
  g,
  guardLatencyMs,
}: {
  g: GuardStatus
  guardLatencyMs?: number
}) {
  const [open, setOpen] = useState(false)
  const decColor =
    g.decision === 'PASS'   ? 'text-dam-green'  :
    g.decision === 'CLAMP'  ? 'text-dam-blue'   :
    g.decision === 'FAULT'  ? 'text-dam-red'    : 'text-dam-orange'

  return (
    <div
      className="group/item bg-dam-surface-3 border border-dam-border rounded hover:border-dam-blue/30 transition-colors overflow-hidden"
    >
      {/* Header row — always visible, click to expand */}
      <button
        type="button"
        className="flex items-center gap-2 p-2 cursor-pointer select-none w-full text-left"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-dam-muted/50 shrink-0">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
        <span className="text-[10px] bg-dam-surface-1 px-1 rounded border border-dam-border text-dam-muted shrink-0">
          {g.layer}
        </span>
        <span className="text-xs font-bold text-dam-text font-mono flex-1 truncate">{g.name}</span>
        {guardLatencyMs !== undefined && (
          <span className="text-[9px] font-mono text-dam-muted shrink-0 ml-1">
            {guardLatencyMs.toFixed(1)} ms
          </span>
        )}
        <span className={`text-[10px] font-bold px-1.5 rounded-sm shrink-0 ${decColor}`}>
          {g.decision}
        </span>
      </button>

      {/* Expanded: full reason + latency detail */}
      {open && (
        <div className="border-t border-dam-border/40 px-3 pb-2 pt-1.5 space-y-1 bg-black/20">
          {g.reason ? (
            <p className="text-[11px] text-dam-muted font-mono leading-relaxed whitespace-pre-wrap">
              {g.reason}
            </p>
          ) : (
            <p className="text-[11px] text-dam-muted/50 italic">Safety check passed — no violation details.</p>
          )}
          {guardLatencyMs !== undefined && (
            <p className="text-[10px] text-dam-muted/60 font-mono pt-1 border-t border-dam-border/20">
              Guard execution time: <span className="text-dam-text">{guardLatencyMs.toFixed(2)} ms</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── View MCAP Button ─────────────────────────────────────────────────────

function ViewMcapButton({ cycleId, tsNs }: { cycleId: number; tsNs: number }) {
  const router = useRouter()
  const [searching, setSearching] = React.useState(false)

  const handleClick = async () => {
    setSearching(true)
    try {
      const result = await api.findMcapSession(cycleId)
      if (result.found && result.filename) {
        router.push(`/mcap-viewer?filename=${encodeURIComponent(result.filename)}&cycle_id=${cycleId}&ts_ns=${tsNs}`)
      } else {
        // No session contains this cycle_id — navigate without filename
        router.push(`/mcap-viewer?cycle_id=${cycleId}&ts_ns=${tsNs}`)
      }
    } catch {
      router.push(`/mcap-viewer?cycle_id=${cycleId}&ts_ns=${tsNs}`)
    } finally {
      setSearching(false)
    }
  }

  return (
    <button
      onClick={() => { handleClick() }}
      disabled={searching}
      className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 bg-dam-blue/10 border border-dam-blue/30 text-dam-blue text-xs font-bold rounded hover:bg-dam-blue/20 disabled:opacity-60 transition-colors"
    >
      {searching
        ? <><span className="w-3 h-3 border-2 border-dam-blue/40 border-t-dam-blue rounded-full animate-spin" /> Locating…</>
        : <><Play size={12} /> View in MCAP Viewer</>
      }
    </button>
  )
}

// ── Main table component ──────────────────────────────────────────────────

export function RiskLogTable() {
  const searchParams = useSearchParams()
  const targetCycleId = searchParams ? Number(searchParams.get('cycle_id') ?? '') || null : null

  const [events, setEvents] = useState<RiskEvent[]>([])
  const [, setStats] = useState<RiskLogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [, setAutoRefresh] = useState(true)
  const [frozen, setFrozen] = useState(false)
  const [filters, setFilters] = useState({
    min_risk_level: '',
    rejected_only: false,
    clamped_only: false,
    limit: 200,
  })
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())
  const highlightRef = useRef<HTMLTableRowElement | null>(null)
  const didAutoExpand = useRef(false)

  const toggleExpand = (key: string) => {
    setExpandedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const load = async (isBackground = false) => {
    if (!isBackground) setLoading(true)
    try {
      const [evRes, stRes] = await Promise.all([
        api.getRiskLog({
          min_risk_level: filters.min_risk_level || undefined,
          rejected_only: filters.rejected_only,
          clamped_only: filters.clamped_only,
          limit: filters.limit,
        }),
        api.getRiskLogStats(),
      ])
      setEvents(evRes.events)
      setStats(stRes)
    } catch { /* ignore */ } finally {
      if (!isBackground) setLoading(false)
    }
  }

  // Listen for global live refresh toggle
  useEffect(() => {
    const sync = () => {
      const saved = localStorage.getItem('dam_live_refresh')
      setAutoRefresh(saved !== 'false')
    }
    sync()
    window.addEventListener('dam_live_refresh_change', sync)
    return () => window.removeEventListener('dam_live_refresh_change', sync)
  }, [])

  // Auto-refresh: Switch to event-driven instead of constant polling
  useEffect(() => {
    load()

    const handleUpdate = () => { load(true) }
    const handleFocus = () => { load() }

    window.addEventListener('dam-system-update', handleUpdate)
    window.addEventListener('focus', handleFocus)

    return () => {
      window.removeEventListener('dam-system-update', handleUpdate)
      window.removeEventListener('focus', handleFocus)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frozen, filters])

  // Grouping logic: collapse consecutive identical results.
  const groupEvents = (raw: RiskEvent[]) => {
    if (raw.length === 0) return []
    const groups: (RiskEvent & { count: number; allCycleIds: number[] })[] = []
    for (const ev of raw) {
      if (groups.length === 0) {
        groups.push({ ...ev, count: 1, allCycleIds: [ev.cycle_id] })
        continue
      }
      const last = groups[groups.length - 1]
      const isSame =
        last.risk_level === ev.risk_level &&
        last.was_rejected === ev.was_rejected &&
        last.was_clamped === ev.was_clamped &&
        last.fallback_triggered === ev.fallback_triggered
      if (isSame) {
        last.count++
        last.allCycleIds.push(ev.cycle_id)
      } else {
        groups.push({ ...ev, count: 1, allCycleIds: [ev.cycle_id] })
      }
    }
    return groups
  }

  // Auto-expand + scroll to target cycle when URL param is set.
  useEffect(() => {
    if (!targetCycleId || didAutoExpand.current || events.length === 0) return
    const gList = groupEvents(events)
    const idx = gList.findIndex(g => g.allCycleIds.includes(targetCycleId))
    if (idx === -1) return
    const g = gList[idx]
    const key = `${idx}:${g.risk_level}-${g.was_rejected}-${g.was_clamped}-${g.fallback_triggered ?? ''}`
    setExpandedKeys(prev => new Set(prev).add(key))
    didAutoExpand.current = true
    setTimeout(() => {
      highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 150)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetCycleId, events])

  const groupedEvents = groupEvents(events)

  return (
    <div className="space-y-4">
      {/* Filter + export bar */}
      <div className="flex flex-wrap items-center gap-3 bg-dam-surface-1 p-2 border border-dam-border rounded">
        <select
          value={filters.min_risk_level}
          onChange={e => setFilters(f => ({ ...f, min_risk_level: e.target.value }))}
          className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5"
        >
          <option value="">All risk levels</option>
          <option value="ELEVATED">≥ ELEVATED</option>
          <option value="CRITICAL">≥ CRITICAL</option>
          <option value="EMERGENCY">EMERGENCY only</option>
        </select>

        <div className="h-6 w-px bg-dam-border/40 mx-1" />

        <label className="flex items-center gap-1.5 text-xs text-dam-muted cursor-pointer">
          <input
            type="checkbox"
            checked={filters.rejected_only}
            onChange={e => setFilters(f => ({ ...f, rejected_only: e.target.checked }))}
            className="accent-dam-blue"
          />
          {' '}Rejected
        </label>
        <label className="flex items-center gap-1.5 text-xs text-dam-muted cursor-pointer">
          <input
            type="checkbox"
            checked={filters.clamped_only}
            onChange={e => setFilters(f => ({ ...f, clamped_only: e.target.checked }))}
            className="accent-dam-blue"
          />
          {' '}Clamped
        </label>

        <div className="h-6 w-px bg-dam-border/40 mx-1" />

        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setFrozen(v => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 border text-xs font-bold rounded transition-colors ${
              frozen
                ? 'bg-dam-orange/10 border-dam-orange/40 text-dam-orange hover:bg-dam-orange/20'
                : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:text-dam-text'
            }`}
          >
            {frozen ? <><Play size={11} /> Resume</> : <><Pause size={11} /> Freeze</>}
          </button>
          <a
            href={api.exportRiskLogJsonUrl()}
            download="risk_log.json"
            className="invisible sm:visible flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
          >
            <Download size={11} /> JSON
          </a>
          <a
            href={api.exportRiskLogCsvUrl()}
            download="risk_log.csv"
            className="invisible sm:visible flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
          >
            <Download size={11} /> CSV
          </a>
          <button
            onClick={() => { load() }}
            disabled={loading}
            className="px-4 py-1.5 bg-dam-blue text-white text-xs font-bold rounded hover:bg-dam-blue-bright disabled:opacity-50 transition-colors"
          >
            {loading ? '...' : 'Reload'}
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto border border-dam-border rounded-lg bg-dam-surface-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-dam-surface-2 border-b border-dam-border text-dam-muted">
              <th className="w-8 px-4 py-2"></th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">ID</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Time</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Cycle</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Risk</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Outcome</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Details</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Lat</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-dam-border/40">
            {groupedEvents.length === 0 ? (
              <tr><td colSpan={8} className="text-center text-dam-muted py-12">No risk events recorded.</td></tr>
            ) : groupedEvents.map((e, i) => {
              const groupKey = `${i}:${e.risk_level}-${e.was_rejected}-${e.was_clamped}-${e.fallback_triggered ?? ''}`
              const isExpanded = expandedKeys.has(groupKey)
              const isTarget = targetCycleId !== null && e.cycle_id === targetCycleId
              return (
                <React.Fragment key={groupKey}>
                  <tr
                    ref={isTarget ? highlightRef : undefined}
                    onClick={() => toggleExpand(groupKey)}
                    className={`hover:bg-dam-surface-2/60 transition-colors group cursor-pointer border-l-2 ${
                      isTarget
                        ? 'bg-dam-blue/5 border-dam-blue animate-pulse-once'
                        : isExpanded
                          ? 'bg-dam-surface-2 border-dam-blue'
                          : 'border-transparent'
                    }`}
                  >
                    <td className="px-4 py-3 text-center text-dam-muted">
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </td>
                    <td className="px-4 py-3 text-dam-muted font-mono">{e.event_id}</td>
                    <td className="px-4 py-3 font-mono text-dam-muted">{new Date(e.timestamp * 1000).toLocaleTimeString()}</td>
                    <td className="px-4 py-3 font-mono text-dam-text">{e.cycle_id}</td>
                    <td className="px-4 py-3"><RiskBadge level={e.risk_level as RiskLevel} /></td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        {e.was_rejected && <span className="px-1.5 py-0.5 rounded bg-orange-950/40 text-dam-orange border border-orange-900/40 font-bold text-[10px]">REJECTED</span>}
                        {e.was_clamped && <span className="px-1.5 py-0.5 rounded bg-blue-950/40 text-dam-blue border border-blue-900/40 font-bold text-[10px]">CLAMPED</span>}
                        {!e.was_rejected && !e.was_clamped && <span className="text-dam-muted">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <span className={`font-mono truncate max-w-[120px] text-[10px] ${e.fallback_triggered ? 'text-dam-orange' : 'text-dam-muted'}`}>
                          {e.fallback_triggered ? `Fallback: ${e.fallback_triggered}` : (e.guard_results?.[0]?.reason || '—')}
                        </span>
                        {e.count > 1 && (
                          <span className="px-1.5 py-0.5 rounded-full bg-dam-surface-3 border border-dam-border text-[10px] text-dam-blue font-bold">
                            ×{e.count}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-dam-muted">{e.latency_ms['total']?.toFixed(1) ?? '—'}<span className="text-[9px] opacity-60">ms</span></td>
                  </tr>

                  {isExpanded && (
                    <tr className="bg-dam-surface-2/40 animate-in slide-in-from-top-1 duration-200">
                      <td colSpan={8} className="px-8 py-4 border-l-2 border-dam-blue">
                        <div className="space-y-4">
                          {/* Header section */}
                          <div className="flex flex-wrap gap-4 text-[10px] text-dam-muted uppercase tracking-widest border-b border-dam-border/40 pb-2">
                            <div className="flex items-center gap-1.5"><Hash size={12} /> Trace: <span className="text-dam-text font-mono select-all uppercase">{e.trace_id}</span></div>
                            <div className="flex items-center gap-1.5"><Clock size={12} /> Full Time: <span className="text-dam-text font-mono">{new Date(e.timestamp * 1000).toISOString()}</span></div>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            {/* Guard Enforcement Details — includes per-guard latency */}
                            <div className="space-y-2">
                              <h4 className="flex items-center gap-2 text-xs font-bold text-dam-blue uppercase tracking-wider mb-2">
                                <Shield size={14} /> Guard Enforcement Details
                              </h4>
                              <div className="space-y-1.5">
                                {e.guard_results && e.guard_results.length > 0 ? e.guard_results.map((g, gi) => (
                                  <GuardResultItem
                                    key={gi}
                                    g={g}
                                    guardLatencyMs={e.perf?.guards?.[g.name]}
                                  />
                                )) : (
                                  <p className="text-xs text-dam-muted italic">No specific guard results logged.</p>
                                )}
                              </div>
                            </div>

                            {/* Performance Breakdown */}
                            <div className="space-y-2">
                              <h4 className="flex items-center gap-2 text-xs font-bold text-dam-orange uppercase tracking-wider mb-2">
                                <Activity size={14} /> Frame Processing Latency
                              </h4>

                              {e.perf ? (
                                <div className="p-3 bg-dam-surface-3 border border-dam-border rounded">
                                  <PerfDetail
                                    perf={e.perf}
                                    totalMs={e.latency_ms['total'] ?? e.perf.stages['total'] ?? 0}
                                  />
                                </div>
                              ) : (
                                <div className="space-y-1 p-2 bg-dam-surface-3 border border-dam-border rounded font-mono">
                                  {Object.entries(e.latency_ms).sort((a, b) => {
                                    if (a[0] === 'total') return 1
                                    if (b[0] === 'total') return -1
                                    return b[1] - a[1]
                                  }).map(([k, v]) => (
                                    <div key={k} className="flex items-center justify-between text-[11px] py-0.5 border-b border-dam-border/20 last:border-0">
                                      <span className="text-dam-muted">{LATENCY_KEY_LABELS[k] ?? k}</span>
                                      <span className={k === 'total' ? 'text-dam-orange font-bold' : 'text-dam-text'}>
                                        {v.toFixed(1)} ms
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}

                              {e.fallback_triggered && (
                                <div className="mt-2 p-2 bg-dam-orange/10 border border-dam-orange/30 rounded">
                                  <p className="text-[10px] font-bold text-dam-orange uppercase">Active Fallback</p>
                                  <p className="text-xs text-dam-orange font-mono font-bold mt-1">➔ {e.fallback_triggered}</p>
                                </div>
                              )}

                              {/* View MCAP button */}
                              <ViewMcapButton cycleId={e.cycle_id} tsNs={Math.floor(e.timestamp * 1e9)} />
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
