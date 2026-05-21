'use client'
import React, { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { McapCycleDetail } from '@/lib/api'
import { CycleSafetyInspector } from '@/components/CycleSafetyInspector'
import {
  ChevronDown, ChevronRight, Loader2,
  Activity, Shield, Cpu, Eye, AlertTriangle,
} from 'lucide-react'

// ── Sub-components ────────────────────────────────────────────────────────

function SectionHeader({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: React.ElementType
  label: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-2 px-3 py-2 hover:bg-dam-surface-2 transition-colors text-left border-t border-dam-border/30 first:border-t-0"
    >
      {open ? <ChevronDown size={12} className="text-dam-muted" /> : <ChevronRight size={12} className="text-dam-muted" />}
      <Icon size={12} className="text-dam-muted shrink-0" />
      <span className="text-[10px] font-bold text-dam-muted uppercase tracking-widest">{label}</span>
    </button>
  )
}

/** Horizontal progress bar for a latency stage. */
function LatencyBar({
  label,
  ms,
  totalMs,
  color = '#3B82F6',
  bold = false,
}: {
  label: string
  ms: number
  totalMs: number
  color?: string
  bold?: boolean
}) {
  const pct = totalMs > 0 ? Math.min((ms / totalMs) * 100, 100) : 0
  return (
    <div className="flex items-center gap-2">
      <span className={`w-16 shrink-0 text-[10px] ${bold ? 'font-bold text-dam-text' : 'text-dam-muted'}`}>
        {label}
      </span>
      <div className="flex-1 h-1.5 bg-dam-surface-3 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className={`w-14 text-right font-mono text-[10px] ${bold ? 'font-bold text-dam-orange' : 'text-dam-text'}`}>
        {ms.toFixed(2)} ms
      </span>
    </div>
  )
}

/** Mini sparkline for an array of floats. */
function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (!values || values.length === 0) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const w = 80
  const h = 24
  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * w
    const y = h - ((v - min) / range) * h
    return `${x},${y}`
  }).join(' ')

  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-dam-muted truncate w-24 shrink-0">{label}</span>
      <svg width={w} height={h} className="shrink-0">
        <polyline points={pts} fill="none" stroke="#3B82F6" strokeWidth="1.5" strokeLinejoin="round" />
      </svg>
      <span className="text-[10px] font-mono text-dam-muted">
        [{values[0]?.toFixed(3)}, …, {values.at(-1)?.toFixed(3)}]
      </span>
    </div>
  )
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'number')
}

function JsonTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null) return <span className="text-dam-muted/60">null</span>
  if (typeof value === 'number') return <span className="font-mono text-dam-text">{value.toFixed(Math.abs(value) < 10 ? 4 : 2)}</span>
  if (typeof value === 'boolean') return <span className="font-mono text-dam-blue">{String(value)}</span>
  if (typeof value === 'string') return <span className="font-mono text-dam-text/90 break-all">{value}</span>
  if (Array.isArray(value)) {
    if (isNumberArray(value)) {
      return <Sparkline values={value} label={`array[${value.length}]`} />
    }
    return (
      <div className="space-y-1">
        {value.map((item, idx) => (
          <div key={idx} className="flex gap-2">
            <span className="font-mono text-[10px] text-dam-muted">[{idx}]</span>
            <JsonTree value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    )
  }
  if (typeof value === 'object') {
    // Stacked key/value rows — narrow widths (live status sidebar) were
    // overflowing the fixed 130px key column into the value, gluing the
    // two together visually.
    return (
      <div className={`space-y-1.5 min-w-0 ${depth > 0 ? 'pl-2 border-l border-dam-border/40' : ''}`}>
        {Object.entries(value as Record<string, unknown>).map(([key, val]) => (
          <div key={key} className="min-w-0">
            <div className="text-[10px] text-dam-muted/80 font-mono break-all" title={key}>{key}</div>
            <div className="min-w-0 break-all"><JsonTree value={val} depth={depth + 1} /></div>
          </div>
        ))}
      </div>
    )
  }
  return <span className="font-mono text-dam-muted">{String(value)}</span>
}

const DECISION_STYLE: Record<string, string> = {
  PASS:   'text-green-400 bg-green-500/10 border-green-500/20',
  CLAMP:  'text-dam-blue bg-blue-500/10 border-blue-500/20',
  REJECT: 'text-red-400 bg-red-500/10 border-red-500/20',
  FAULT:  'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
}

const STAGE_COLORS: Record<string, string> = {
  source_ms:  '#6366F1',
  policy_ms:  '#F59E0B',
  guards_ms:  '#10B981',
  sink_ms:    '#3B82F6',
}

const LAYER_COLORS: Record<string, string> = {
  L0_ms: '#A78BFA',
  L1_ms: '#34D399',
  L2_ms: '#F97316',
  L3_ms: '#F87171',
}

// ── Main component ────────────────────────────────────────────────────────

interface McapCycleInspectorProps {
  readonly filename: string | null
  readonly cycleId: number | null
  readonly tsNs?: number | null
  /** Fallback cycle data from telemetry when MCAP file doesn't have this cycle yet (live mode) */
  readonly fallbackDetail?: Partial<McapCycleDetail> | null
  /**
   * When provided, skip the MCAP API call entirely and display this data directly.
   * Used in live mode where cycle data comes from WebSocket telemetry.
   */
  readonly overrideCycleDetail?: McapCycleDetail | null
}

export function McapCycleInspector({ filename, cycleId, tsNs, fallbackDetail, overrideCycleDetail }: McapCycleInspectorProps) {
  const [detail, setDetail] = useState<McapCycleDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [usingFallback, setUsingFallback] = useState(false)

  const [open, setOpen] = useState({
    guards:  true,
    latency: false,
    obs:     false,
    action:  false,
    failure: false,
  })
  const toggle = (k: keyof typeof open) => setOpen(p => ({ ...p, [k]: !p[k] }))

  useEffect(() => {
    // If an override is provided, skip the API call entirely
    if (overrideCycleDetail !== undefined) {
      setDetail(overrideCycleDetail)
      setUsingFallback(true)
      setLoading(false)
      setError(null)
      return
    }

    if (!filename || cycleId == null) {
      setDetail(null)
      setUsingFallback(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    setUsingFallback(false)

    api.getMcapCycleDetail(filename, cycleId, tsNs)
      .then(d => { if (!cancelled) setDetail(d) })
      .catch(e => {
        // If MCAP file doesn't have this cycle yet (live mode), use fallback
        if (!cancelled) {
          if (fallbackDetail && typeof fallbackDetail === 'object' && 'cycle_id' in fallbackDetail) {
            setDetail(fallbackDetail as McapCycleDetail)
            setUsingFallback(true)
          } else {
            setError(e instanceof Error ? e.message : 'Load failed')
          }
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filename, cycleId, fallbackDetail, overrideCycleDetail])

  if (!overrideCycleDetail && (!filename || cycleId == null)) {
    // If we have a filename but no cycleId, it means we are likely transitioning
    // and about to auto-select the first cycle. Show the skeleton instead of empty text.
    if (filename) {
      return (
        <div className="h-full flex flex-col bg-dam-surface-1 border border-dam-border rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between mb-4">
            <div className="h-4 w-32 bg-dam-surface-3 rounded animate-pulse" />
            <div className="h-4 w-16 bg-dam-surface-3 rounded animate-pulse" />
          </div>
          <div className="mt-auto flex items-center justify-center gap-2 text-dam-muted/60">
            <Loader2 size={12} className="animate-spin" />
            <span className="text-[10px] uppercase tracking-wider font-bold">Synchronizing...</span>
          </div>
        </div>
      )
    }

    return (
      <div className="h-full flex items-center justify-center text-dam-muted text-sm py-12">
        Select a cycle in the timeline
      </div>
    )
  }

  if (loading && !detail) {
    return (
      <div className="h-full flex flex-col bg-dam-surface-1 border border-dam-border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between mb-4">
          <div className="h-4 w-32 bg-dam-surface-3 rounded animate-pulse" />
          <div className="h-4 w-16 bg-dam-surface-3 rounded animate-pulse" />
        </div>
        {[1, 2, 3].map(i => (
          <div key={i} className="space-y-2">
            <div className="h-3 w-24 bg-dam-surface-2 rounded animate-pulse" />
            <div className="h-8 w-full bg-dam-surface-2 rounded animate-pulse" />
          </div>
        ))}
        <div className="mt-auto flex items-center justify-center gap-2 text-dam-muted/60">
          <Loader2 size={12} className="animate-spin" />
          <span className="text-[10px] uppercase tracking-wider font-bold">Synchronizing...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg space-y-2">
        <p className="font-bold">{error}</p>
        <p className="text-red-400/80 italic">
          Tip: Please stop the system and refresh the page to ensure all telemetry data is synchronized.
        </p>
      </div>
    )
  }

  if (!detail) return null

  const totalMs = detail.total_ms || detail.latency?.total_ms || 0
  const cycleStatusLabel = detail.has_violation ? 'REJECT' : detail.has_clamp ? 'CLAMP' : 'PASS'
  const layerTags = [
    ...detail.violated_layers.map(layer => ({ layer, tone: 'red' as const })),
    ...detail.clamped_layers.map(layer => ({ layer, tone: 'blue' as const })),
  ]
  const inspectorGuards = detail.guard_results.map(g => ({
    name: g.guard_name,
    layer: g.layer_name || `L${g.layer}`,
    decision: g.decision_name,
    reason: g.reason,
    latency_ms: g.latency_ms,
    is_violation: g.is_violation,
    is_clamp: g.is_clamp,
    metadata: g.metadata,
  }))

  return (
    <div className="flex flex-col h-full min-h-0 bg-dam-surface-1 border border-dam-border rounded-lg text-xs overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-dam-border/50 bg-dam-surface-2/50">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-dam-text shrink-0">Cycle <span className="text-dam-blue">#{detail.cycle_id}</span></span>
          {detail.active_task && (
            <span className="min-w-0 truncate text-[10px] text-dam-muted/70">
              · {detail.active_task}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5 flex-wrap justify-end">
            {usingFallback && (
              <span className="px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase bg-amber-500/10 border-amber-500/20 text-amber-400">
                Live
              </span>
            )}
            <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${
              detail.has_violation ? 'text-red-400 bg-red-500/10 border-red-500/20' :
              detail.has_clamp     ? 'text-dam-blue bg-blue-500/10 border-blue-500/20' :
                                     'text-green-400 bg-green-500/10 border-green-500/20'
            }`}>
              {cycleStatusLabel}
            </span>
            {layerTags.map(({ layer, tone }) => (
              <span
                key={`${tone}-${layer}`}
                className={`px-1.5 py-0.5 rounded font-mono text-[9px] border ${
                  tone === 'red'
                    ? 'bg-red-500/20 text-red-400 border-red-500/20'
                    : 'bg-blue-500/20 text-dam-blue border-blue-500/20'
                }`}
              >
                {layer}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1">
        <div className="h-full min-h-0">
          <CycleSafetyInspector
            chrome="flush"
            mode="mcap"
            guards={inspectorGuards}
            latency={detail.latency}
            totalMs={totalMs}
            observation={detail.observation}
            action={detail.action}
            failure={{
              type: detail.failure_type,
              guardNames: detail.failure_guard_names,
              layers: detail.failure_layers,
              decisions: detail.failure_decisions,
              reasons: detail.failure_reasons,
              tuple: detail.failure_tuple,
            }}
          />
        </div>
      </div>
    </div>
  )
}
