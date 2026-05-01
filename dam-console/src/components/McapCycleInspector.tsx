'use client'
import React, { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { McapCycleDetail } from '@/lib/api'
import {
  ChevronDown, ChevronRight, Loader2,
  Activity, Shield, Cpu, Eye,
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
      <div className="p-4 text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg">
        {error}
      </div>
    )
  }

  if (!detail) return null

  const totalMs = detail.total_ms || detail.latency?.total_ms || 0
  const cycleStatusLabel = detail.has_violation ? 'REJECT' : detail.has_clamp ? 'CLAMP' : 'PASS'

  return (
    <div className="flex flex-col h-full overflow-y-auto bg-dam-surface-1 border border-dam-border rounded-lg text-xs">
      {/* Header */}
      <div className="px-3 py-2.5 border-b border-dam-border/50 bg-dam-surface-2/50">
        <div className="flex items-center justify-between">
          <span className="font-mono font-bold text-dam-text">Cycle <span className="text-dam-blue">#{detail.cycle_id}</span></span>
          <div className="flex items-center gap-1.5">
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
          </div>
        </div>
        <p className="text-[10px] text-dam-muted/60 mt-0.5">
          {new Date(detail.timestamp_ns / 1_000_000).toLocaleString([], { timeStyle: 'medium' })}
          {detail.active_task && <span className="ml-2 text-dam-text/50">· {detail.active_task}</span>}
        </p>
        {(detail.violated_layers.length > 0 || detail.clamped_layers.length > 0) && (
          <div className="flex gap-1 mt-1.5 flex-wrap">
            {detail.violated_layers.map(l => (
              <span key={l} className="px-1.5 py-0.5 rounded font-mono text-[9px] bg-red-500/20 text-red-400 border border-red-500/20">{l}</span>
            ))}
            {detail.clamped_layers.map(l => (
              <span key={l} className="px-1.5 py-0.5 rounded font-mono text-[9px] bg-blue-500/20 text-dam-blue border border-blue-500/20">{l}</span>
            ))}
          </div>
        )}
      </div>

      {/* Guard results */}
      <SectionHeader icon={Shield} label={`Guards (${detail.guard_results.length})`} open={open.guards} onToggle={() => toggle('guards')} />
      {open.guards && (
        <div className="px-3 py-2 space-y-1.5">
          {detail.guard_results.length === 0 ? (
            <p className="text-dam-muted/60 italic text-[10px]">No guard results recorded</p>
          ) : detail.guard_results.map((g, i) => (
            <div key={`${g.layer}-${i}`} className={`flex items-start gap-2 p-2 rounded border ${
              g.is_violation ? 'bg-red-500/5 border-red-500/20' :
              g.is_clamp     ? 'bg-blue-500/5 border-blue-500/20' :
                               'bg-dam-surface-2 border-dam-border/40'
            }`}>
              <span className="font-mono text-[9px] text-dam-muted bg-dam-surface-1 px-1 py-0.5 rounded border border-dam-border shrink-0 mt-0.5">
                L{g.layer}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono text-[10px] font-bold text-dam-text truncate">{g.guard_name}</span>
                  <span className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase shrink-0 ${DECISION_STYLE[g.decision_name] ?? 'text-dam-muted border-dam-border'}`}>
                    {g.decision_name}
                  </span>
                </div>
                {g.reason && (
                  <p className="text-[10px] text-dam-muted/70 mt-0.5 truncate" title={g.reason}>{g.reason}</p>
                )}
                {g.latency_ms != null && (
                  <p className="text-[9px] text-dam-muted/50 font-mono mt-0.5">{g.latency_ms.toFixed(2)} ms</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Latency */}
      <SectionHeader icon={Activity} label="Latency" open={open.latency} onToggle={() => toggle('latency')} />
      {open.latency && (
        <div className="px-3 py-2 space-y-1.5">
          {(['source_ms', 'policy_ms', 'guards_ms', 'sink_ms'] as const).map(key => (
            <LatencyBar
              key={key}
              label={key.replaceAll('_ms', '')}
              ms={detail.latency?.[key] ?? detail[key] ?? 0}
              totalMs={totalMs}
              color={STAGE_COLORS[key]}
            />
          ))}
          {/* Total: number only — the bar is always 100% wide and adds no information */}
          <div className="flex items-center gap-2 pt-0.5 mt-0.5 border-t border-dam-border/40">
            <span className="w-16 shrink-0 text-[10px] font-bold text-dam-text">total</span>
            <span className="flex-1" />
            <span className="font-mono text-[10px] font-bold text-dam-orange">
              {totalMs.toFixed(2)} ms
            </span>
          </div>
          {/* Per-layer breakdown */}
          {(['L0_ms', 'L1_ms', 'L2_ms', 'L3_ms'] as const).some(k => (detail.latency?.[k] ?? 0) > 0) && (
            <div className="pt-1.5 border-t border-dam-border/30 space-y-1.5 mt-1">
              <p className="text-[9px] font-bold text-dam-muted/60 uppercase tracking-wider">Per Layer</p>
              {(['L0_ms', 'L1_ms', 'L2_ms', 'L3_ms'] as const).map(key => {
                const ms = detail.latency?.[key] ?? 0
                if (ms === 0) return null
                return (
                  <LatencyBar key={key} label={key.replaceAll('_ms', '')} ms={ms} totalMs={totalMs} color={LAYER_COLORS[key]} />
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Observation */}
      <SectionHeader icon={Eye} label="Observation" open={open.obs} onToggle={() => toggle('obs')} />
      {open.obs && (
        <div className="px-3 py-2 space-y-2">
          {!detail.observation ? (
            <p className="text-dam-muted/60 italic text-[10px]">No observation data</p>
          ) : (
            <>
              <Sparkline values={detail.observation.joint_positions} label="joint_positions" />
              {detail.observation.joint_velocities && (
                <Sparkline values={detail.observation.joint_velocities} label="joint_velocities" />
              )}
              {detail.observation.end_effector_pose && (
                <Sparkline values={detail.observation.end_effector_pose} label="ee_pose" />
              )}
              {detail.observation.force_torque && (
                <Sparkline values={detail.observation.force_torque} label="force_torque" />
              )}
            </>
          )}
        </div>
      )}

      {/* Action */}
      <SectionHeader icon={Cpu} label="Action" open={open.action} onToggle={() => toggle('action')} />
      {open.action && (
        <div className="px-3 py-2 space-y-2">
          {!detail.action ? (
            <p className="text-dam-muted/60 italic text-[10px]">No action data</p>
          ) : (
            <>
              <Sparkline values={detail.action.target_positions} label="target_pos" />
              {detail.action.target_velocities && (
                <Sparkline values={detail.action.target_velocities} label="target_vel" />
              )}
              {detail.action.validated_positions && (
                <Sparkline values={detail.action.validated_positions} label="validated_pos" />
              )}
              {detail.action.was_clamped && (
                <div className="px-2 py-1 bg-dam-blue/10 border border-dam-blue/20 rounded text-[10px] text-dam-blue font-bold">
                  Action was clamped by safety system
                  {detail.action.fallback_triggered && <span className="ml-1 opacity-70">→ {detail.action.fallback_triggered}</span>}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
