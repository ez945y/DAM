'use client'
import { useMemo, useState } from 'react'
import { Activity, AlertTriangle, ChevronDown, ChevronRight, Cpu, Shield } from 'lucide-react'
import type { FailureType } from '@/lib/types'
import { useDisplayUnit, type DisplayUnit } from '@/hooks/useDisplayUnit'

export interface InspectorGuardResult {
  name: string
  layer: string
  event_class?: string
  decision: string
  reason?: string | null
  latency_ms?: number | null
  is_violation?: boolean
  is_clamp?: boolean
  metadata?: Record<string, unknown>
}

export interface CycleSafetyInspectorProps {
  readonly guards: InspectorGuardResult[]
  readonly latency?: Record<string, number>
  readonly totalMs?: number
  readonly failure?: {
    type?: FailureType | string | null
    guardNames?: string[]
    layers?: string[]
    decisions?: string[]
    reasons?: string[]
    tuple?: Record<string, unknown> | null
  }
  readonly observation?: unknown
  readonly action?: unknown
  readonly mode?: 'mcap' | 'risk'
  readonly chrome?: 'card' | 'flush'
  /**
   * Optional override for the display unit.  Normally the inspector reads it
   * from ``DisplayUnitContext`` (mounted at the app root) so prop drilling
   * isn't required.  Passing this explicitly lets tests or one-off panels
   * force a unit independent of the active stackfile.
   */
  readonly displayUnit?: DisplayUnit
}

const RAD2DEG = 180.0 / Math.PI

function makeJointFormatter(unit: 'rad' | 'deg'): { scale: (x: number) => number; label: string } {
  // Function injection: bind once at component init, no per-cell branch.
  if (unit === 'deg') return { scale: x => x * RAD2DEG, label: 'deg' }
  return { scale: x => x, label: 'rad' }
}

/**
 * Walk a guard-metadata object and apply ``scale`` to fields whose unit
 * (declared by the server in a sibling ``_units`` map) starts with "rad".
 *
 * The server emits ``_units`` alongside the rad-bearing payload — either
 * flat keys (``"upper": "rad"``) or dotted paths into a nested dataclass
 * (``"motion_qp.upper": "rad"``).  The frontend reads that map directly
 * instead of mirroring a hardcoded key list, so adding a new callback
 * that emits rad-bearing metadata needs zero frontend changes.
 *
 * Anything without a unit entry is rendered as-is.  Generic so callers
 * keep their input type.
 */
function scaleRadMetadata<T>(obj: T, scale: (x: number) => number): T {
  return walkAndScale(obj, scale) as T
}

function walkAndScale(obj: unknown, scale: (x: number) => number): unknown {
  if (Array.isArray(obj)) return obj.map(v => walkAndScale(v, scale))
  if (obj == null || typeof obj !== 'object') return obj

  const src = obj as Record<string, unknown>
  const units = src._units && typeof src._units === 'object' && !Array.isArray(src._units)
    ? (src._units as Record<string, string>)
    : null

  // First pass: recurse into children, dropping the meta key from the output.
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(src)) {
    if (k === '_units') continue
    out[k] = walkAndScale(v, scale)
  }

  // Second pass: apply per-path unit scaling using the declared map.
  if (units) {
    for (const [path, unit] of Object.entries(units)) {
      if (!unit.startsWith('rad')) continue  // metres / seconds / dimensionless: leave alone
      applyScaleAtPath(out, path.split('.'), scale)
    }
  }
  return out
}

function applyScaleAtPath(
  root: Record<string, unknown>,
  keys: string[],
  scale: (x: number) => number,
): void {
  let parent: Record<string, unknown> | undefined = root
  for (let i = 0; i < keys.length - 1; i++) {
    const next = parent?.[keys[i]]
    if (next == null || typeof next !== 'object' || Array.isArray(next)) return
    parent = next as Record<string, unknown>
  }
  if (!parent) return
  const last = keys[keys.length - 1]
  const val = parent[last]
  if (Array.isArray(val)) {
    parent[last] = val.map(x => typeof x === 'number' ? scale(x) : x)
  } else if (typeof val === 'number') {
    parent[last] = scale(val)
  }
}

// DAM event taxonomy (dam.runtime.failure_classify):
//   ood_only           感知異常事件 — only L0 perception guards fired
//   guard_triggered    動作風險事件 — any non-hardware guard rejected/limited the action
//   hardware_triggered 硬體風險事件 — any L3 / hardware-source guard fired
const FAILURE_LABEL: Record<string, string> = {
  ood_only: 'Perception',
  guard_triggered: 'Action',
  hardware_triggered: 'Hardware',
}

const DECISION_STYLE: Record<string, string> = {
  PASS: 'text-green-400 bg-green-500/10 border-green-500/20',
  CLAMP: 'text-dam-blue bg-blue-500/10 border-blue-500/20',
  REJECT: 'text-red-400 bg-red-500/10 border-red-500/20',
  FAULT: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
}

function fmtNumber(value: number): string {
  return value.toFixed(Math.abs(value) < 10 ? 3 : 1)
}

function isNonEmpty(v: unknown): boolean {
  if (v == null) return false
  if (Array.isArray(v) && v.length === 0) return false
  if (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0) return false
  return true
}

function JsonValue({ value }: { value: unknown }) {
  // null / empty are filtered out by the caller; render nothing as a safety
  // net so a stray null doesn't add a useless "null" line.
  if (!isNonEmpty(value)) return null
  if (typeof value === 'number') return <span className="font-mono text-dam-text">{fmtNumber(value)}</span>
  if (typeof value === 'boolean') return <span className="font-mono text-dam-blue">{String(value)}</span>
  if (typeof value === 'string') return <span className="font-mono text-dam-text/90 break-all">{value}</span>
  if (Array.isArray(value)) {
    const sample = value.slice(0, 8).map(v => typeof v === 'number' ? fmtNumber(v) : String(v)).join(', ')
    return <span className="font-mono text-dam-text/80 break-all">[{sample}{value.length > 8 ? ', …' : ''}]</span>
  }
  if (typeof value === 'object') {
    // Filter null / empty entries — guard callbacks that share a common
    // dataclass (e.g. MotionQPConstraint) leave irrelevant fields at None,
    // and rendering "field: null" for each of them is pure noise.
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => isNonEmpty(v))
    if (entries.length === 0) return null
    return (
      <div className="space-y-0.5 min-w-0">
        {entries.slice(0, 16).map(([k, v]) => {
          const nested = typeof v === 'object' && v !== null && !Array.isArray(v)
          return nested ? (
            <div key={k} className="min-w-0">
              <div className="text-[10px] text-dam-muted/80 font-mono" title={k}>{k}</div>
              <div className="pl-2 mt-0.5 min-w-0"><JsonValue value={v} /></div>
            </div>
          ) : (
            // Inline "key: value" — wraps naturally, no fixed column width
            // and no extra indent line so the layout stays flat.
            <div key={k} className="min-w-0 flex flex-wrap items-baseline gap-x-2">
              <span className="text-[10px] text-dam-muted/80 font-mono shrink-0" title={k}>{k}</span>
              <span className="min-w-0 break-all"><JsonValue value={v} /></span>
            </div>
          )
        })}
        {entries.length > 16 && <p className="text-[10px] text-dam-muted/60">+ {entries.length - 16} more</p>}
      </div>
    )
  }
  return <span className="font-mono text-dam-muted">{String(value)}</span>
}

function numericMap(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1])),
  )
}

function HardwareTable({ metadata }: { metadata: Record<string, unknown> }) {
  const temps = numericMap(metadata.temperatures)
  const currents = numericMap(metadata.currents)
  const voltages = numericMap(metadata.voltages)
  const joints = [...new Set([...Object.keys(temps), ...Object.keys(currents), ...Object.keys(voltages)])]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  if (joints.length === 0) return null

  return (
    <div className="overflow-x-auto rounded border border-dam-border/50">
      <table className="w-full text-[10px]">
        <thead className="bg-dam-surface-2 text-dam-muted uppercase tracking-wider">
          <tr>
            <th className="px-2 py-1 text-left">Joint</th>
            <th className="px-2 py-1 text-right">Temp</th>
            <th className="px-2 py-1 text-right">Current</th>
            <th className="px-2 py-1 text-right">Voltage</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-dam-border/30">
          {joints.map(joint => (
            <tr key={joint}>
              <td className="px-2 py-1 font-mono text-dam-text">{joint}</td>
              <td className="px-2 py-1 text-right font-mono text-dam-text">{temps[joint] == null ? '—' : `${temps[joint].toFixed(0)}°C`}</td>
              <td className="px-2 py-1 text-right font-mono text-dam-text">{currents[joint] == null ? '—' : `${currents[joint].toFixed(2)}A`}</td>
              <td className="px-2 py-1 text-right font-mono text-dam-text">{voltages[joint] == null ? '—' : `${voltages[joint].toFixed(2)}V`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Layer index from "L0".."L3" / "0".."3" / number, tolerant of junk. */
function layerIndex(layer: string): number {
  const m = /\d+/.exec(layer ?? '')
  return m ? Number(m[0]) : 99
}

/** Stable ordering shared by every consumer (MCAP + Risk Log) so the
 *  inspector guard list is identical regardless of backend emit order:
 *  by layer (L0→L3) then guard name. */
function sortGuards(guards: InspectorGuardResult[]): InspectorGuardResult[] {
  return [...guards].sort(
    (a, b) =>
      layerIndex(a.layer) - layerIndex(b.layer) ||
      a.name.localeCompare(b.name),
  )
}

function normalizeGuardMetadata(guard: InspectorGuardResult): Record<string, unknown> {
  const metadata = guard.metadata ?? {}
  const name = guard.name.toLowerCase()
  if (name.includes('host_health')) {
    // Unwrap the redundant outer `host_health` namespace — the card header
    // already says host_health; rendering "host_health > {...}" wastes a
    // level of nesting.
    const inner = metadata.host_health
    if (inner && typeof inner === 'object') return inner as Record<string, unknown>
    return metadata
  }
  return Object.fromEntries(Object.entries(metadata).filter(([key]) => key !== 'host_health'))
}

function GuardCard({ guard, displayUnit }: { guard: InspectorGuardResult; displayUnit: DisplayUnit }) {
  const [open, setOpen] = useState(guard.decision !== 'PASS' || guard.layer === 'L1' || guard.layer === 'L3')
  // Avoid recomputing the entire walk + allocation on unrelated re-renders.
  // Dependencies: the raw guard data + which unit we're displaying.
  const metadata = useMemo(() => {
    const raw = normalizeGuardMetadata(guard)
    const { scale } = makeJointFormatter(displayUnit)
    return scaleRadMetadata(raw, scale)
  }, [guard, displayUnit])
  const hardwareKeys = ['temperatures', 'currents', 'voltages']
  const extraMetadata = Object.fromEntries(Object.entries(metadata).filter(([k]) => !hardwareKeys.includes(k)))

  return (
    <div className={`rounded border overflow-hidden ${guard.decision === 'PASS' ? 'border-dam-border/40 bg-dam-surface-2' : 'border-red-500/20 bg-red-500/5'}`}>
      {/* Header wraps to 2 rows on narrow widths so name + boundary tag aren't
          truncated to a single character. Layer + decision sit on row 1; name
          + latency sit on row 2 when the header has < ~280px of width. */}
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full text-left flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5"
      >
        {open ? <ChevronDown size={12} className="text-dam-muted shrink-0" /> : <ChevronRight size={12} className="text-dam-muted shrink-0" />}
        <span className="w-7 text-[10px] font-mono text-dam-muted shrink-0">{guard.layer}</span>
        <span className="flex-1 min-w-[6rem] font-mono text-[11px] font-bold text-dam-text break-all">{guard.name}</span>
        {guard.latency_ms != null && <span className="font-mono text-[10px] text-dam-muted shrink-0">{guard.latency_ms.toFixed(1)}ms</span>}
        <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-bold ${DECISION_STYLE[guard.decision] ?? 'text-dam-muted border-dam-border'}`}>{guard.decision}</span>
      </button>
      {open && (
        <div className="border-t border-dam-border/40 px-3 py-2 space-y-2 min-w-0">
          {guard.reason && <p className="max-w-full text-[11px] text-dam-muted leading-relaxed whitespace-pre-wrap break-all">{guard.reason}</p>}
          <HardwareTable metadata={metadata} />
          {Object.keys(extraMetadata).length > 0 && (
            <div className="min-w-0">
              <JsonValue value={extraMetadata} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Failure-layer breakdown (which level had the problem) ─────────────────

// Mirrors dam.guard.layer.GuardLayer:
//   L0 OOD · L1 Physical Kinematics · L2 Task Execution · L3 Hardware
const LAYER_LABEL: Record<number, string> = {
  0: 'L0 · Perception (OOD)',
  1: 'L1 · Kinematics',
  2: 'L2 · Task Execution',
  3: 'L3 · Hardware',
}

const DECISION_RANK: Record<string, number> = { PASS: 0, CLAMP: 1, REJECT: 2, FAULT: 3 }

function FailureLayerBreakdown({ failing }: { failing: InspectorGuardResult[] }) {
  const byLayer = new Map<number, { worst: string; guards: InspectorGuardResult[] }>()
  for (const g of failing) {
    const li = layerIndex(g.layer)
    const slot = byLayer.get(li)
    if (!slot) {
      byLayer.set(li, { worst: g.decision, guards: [g] })
      continue
    }
    slot.guards.push(g)
    if ((DECISION_RANK[g.decision] ?? 0) > (DECISION_RANK[slot.worst] ?? 0)) slot.worst = g.decision
  }
  const layers = [...byLayer.entries()].sort((a, b) => a[0] - b[0])
  if (layers.length === 0) return null

  return (
    <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2 space-y-1.5">
      <p className="text-[10px] uppercase tracking-wider text-dam-muted font-bold">Failure layers</p>
      {layers.map(([li, info]) => (
        <div key={li} className="flex items-start gap-2">
          <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-bold ${DECISION_STYLE[info.worst] ?? 'text-dam-muted border-dam-border'}`}>
            {info.worst}
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-bold text-dam-text">{LAYER_LABEL[li] ?? `L${li}`}</p>
            <p className="text-[10px] font-mono text-dam-muted truncate" title={info.guards.map(g => g.name).join(', ')}>
              {info.guards.map(g => g.name).join(', ')}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── I/O panels (full observation / action, target vs validated) ───────────

function numArr(v: unknown): number[] | null {
  return Array.isArray(v) && v.every(x => typeof x === 'number') ? (v as number[]) : null
}

function VectorTable({
  label,
  cols,
}: {
  label: string
  cols: { head: string; values: number[] | null; highlightDiffWith?: number[] | null }[]
}) {
  const present = cols.filter(c => c.values && c.values.length > 0)
  if (present.length === 0) return null
  const n = Math.max(...present.map(c => c.values!.length))
  return (
    <div className="space-y-1">
      <p className="text-[10px] uppercase tracking-wider text-dam-muted font-bold">{label}</p>
      <div className="overflow-x-auto rounded border border-dam-border/50">
        <table className="w-full text-[10px]">
          <thead className="bg-dam-surface-2 text-dam-muted uppercase tracking-wider">
            <tr>
              <th className="px-2 py-1 text-left">J</th>
              {present.map(c => <th key={c.head} className="px-2 py-1 text-right">{c.head}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-dam-border/30">
            {Array.from({ length: n }, (_, j) => (
              <tr key={j}>
                <td className="px-2 py-1 font-mono text-dam-muted">J{j}</td>
                {present.map(c => {
                  const v = c.values![j]
                  const ref = c.highlightDiffWith?.[j]
                  const changed = ref != null && v != null && Math.abs(ref - v) > 1e-6
                  return (
                    <td
                      key={c.head}
                      className={`px-2 py-1 text-right font-mono ${changed ? 'text-dam-orange font-bold bg-dam-orange/10' : 'text-dam-text'}`}
                    >
                      {v == null ? '—' : fmtNumber(v)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ActionPanel({ action, displayUnit = 'rad' }: { action: unknown; displayUnit?: 'rad' | 'deg' }) {
  if (!action || typeof action !== 'object') {
    return <p className="text-[11px] text-dam-muted/60 italic">No action recorded.</p>
  }
  const a = action as Record<string, unknown>
  const { scale, label } = makeJointFormatter(displayUnit)
  const apply = (xs: number[] | null) => (xs ?? []).map(scale)
  const target = apply(numArr(a.target_positions))
  const validated = apply(numArr(a.validated_positions))
  const tvel = apply(numArr(a.target_velocities))
  const vvel = apply(numArr(a.validated_velocities))
  const wasClamped = a.was_clamped === true
  const fallback = typeof a.fallback_triggered === 'string' ? a.fallback_triggered : null
  const known = new Set([
    'target_positions', 'validated_positions', 'target_velocities',
    'validated_velocities', 'was_clamped', 'fallback_triggered',
  ])
  const extra = Object.fromEntries(Object.entries(a).filter(([k]) => !known.has(k)))

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        <span className={`px-1.5 py-0.5 rounded border text-[9px] font-bold ${wasClamped ? 'text-dam-blue bg-blue-500/10 border-blue-500/20' : 'text-dam-muted border-dam-border'}`}>
          {wasClamped ? 'CLAMPED' : 'not clamped'}
        </span>
        {fallback && (
          <span className="px-1.5 py-0.5 rounded border text-[9px] font-bold text-dam-orange bg-orange-500/10 border-orange-500/20">
            fallback: {fallback}
          </span>
        )}
      </div>
      <VectorTable
        label={`Positions (target → validated) [${label}]`}
        cols={[
          { head: 'target', values: target },
          { head: 'validated', values: validated, highlightDiffWith: target },
        ]}
      />
      <VectorTable
        label={`Velocities (target → validated) [${label}/s]`}
        cols={[
          { head: 'target', values: tvel },
          { head: 'validated', values: vvel, highlightDiffWith: tvel },
        ]}
      />
      {Object.keys(extra).length > 0 && (
        <div className="rounded border border-dam-border/40 bg-dam-surface-1 p-2"><JsonValue value={extra} /></div>
      )}
    </div>
  )
}

function ObservationPanel({ observation, displayUnit = 'rad' }: { observation: unknown; displayUnit?: 'rad' | 'deg' }) {
  if (!observation || typeof observation !== 'object') {
    return <p className="text-[11px] text-dam-muted/60 italic">No observation recorded.</p>
  }
  const o = observation as Record<string, unknown>
  const { scale, label } = makeJointFormatter(displayUnit)
  const known = new Set([
    'joint_positions', 'joint_velocities', 'end_effector_pose',
    'force_torque', 'obs_timestamp',
  ])
  const extra = Object.fromEntries(Object.entries(o).filter(([k]) => !known.has(k)))
  return (
    <div className="space-y-2">
      <VectorTable
        label={`Joint state [pos ${label}, vel ${label}/s]`}
        cols={[
          { head: 'position', values: (numArr(o.joint_positions) ?? []).map(scale) },
          { head: 'velocity', values: (numArr(o.joint_velocities) ?? []).map(scale) },
        ]}
      />
      <VectorTable
        label="End-effector pose"
        cols={[{ head: 'value', values: numArr(o.end_effector_pose) }]}
      />
      <VectorTable
        label="Force / torque"
        cols={[{ head: 'value', values: numArr(o.force_torque) }]}
      />
      {Object.keys(extra).length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-dam-muted font-bold">Other channels</p>
          <div className="rounded border border-dam-border/40 bg-dam-surface-1 p-2"><JsonValue value={extra} /></div>
        </div>
      )}
    </div>
  )
}

function LatencySummary({ latency = {}, totalMs }: { latency?: Record<string, number>; totalMs?: number }) {
  const total = totalMs ?? latency.total ?? latency.total_ms ?? 0
  const rows = [
    ['source', latency.source ?? latency.source_ms ?? latency.obs ?? 0],
    ['context', latency.context ?? latency.context_ms ?? 0],
    ['policy', latency.policy ?? latency.policy_ms ?? 0],
    ['guards', latency.guards ?? latency.guards_ms ?? latency.validate ?? 0],
    ['sink', latency.sink ?? latency.sink_ms ?? 0],
  ] as const
  return (
    <div className="space-y-1">
      {rows.map(([label, ms]) => (
        <div key={label} className="grid grid-cols-[54px_1fr_58px] items-center gap-2 text-[10px]">
          <span className="text-dam-muted">{label}</span>
          <div className="h-1.5 bg-dam-surface-3 rounded overflow-hidden">
            <div className="h-full bg-dam-blue" style={{ width: `${total > 0 ? Math.min(100, (ms / total) * 100) : 0}%` }} />
          </div>
          <span className="text-right font-mono text-dam-text">{ms.toFixed(1)}ms</span>
        </div>
      ))}
      <div className="flex justify-between border-t border-dam-border/40 pt-1 text-[10px]">
        <span className="font-bold text-dam-muted">total</span>
        <span className="font-mono font-bold text-dam-orange">{total.toFixed(1)}ms</span>
      </div>
    </div>
  )
}

export function CycleSafetyInspector({ guards, latency, totalMs, failure, observation, action, mode = 'mcap', chrome = 'card', displayUnit }: CycleSafetyInspectorProps) {
  const ctxUnit = useDisplayUnit()
  const unit: DisplayUnit = displayUnit ?? ctxUnit
  const displayGuards = useMemo(() => {
    const hasHostGuard = guards.some(g => g.name.toLowerCase().includes('host_health'))
    const hostHealth = guards
      .map(g => g.metadata?.host_health)
      .find(v => v && typeof v === 'object') as Record<string, unknown> | undefined
    const base = guards.map(g => ({ ...g, metadata: normalizeGuardMetadata(g) }))
    if (hasHostGuard || !hostHealth) return sortGuards(base)
    return sortGuards([
      ...base,
      {
        name: 'host_health',
        layer: 'L3',
        decision: 'PASS',
        reason: null,
        metadata: { host_health: hostHealth },
      },
    ])
  }, [guards])
  const failing = displayGuards.filter(g => g.decision !== 'PASS')
  const [tab, setTab] = useState<'summary' | 'guards' | 'io'>('summary')
  const tabs = [
    ['summary', 'Summary'],
    ['guards', `Guards ${displayGuards.length}`],
    ...(mode === 'mcap' ? [['io', 'I/O']] as const : []),
  ] as const
  const failureLabel = failure?.type ? (FAILURE_LABEL[String(failure.type)] ?? String(failure.type)) : null
  const topReason = failing.find(g => g.reason)?.reason ?? failure?.reasons?.[0]
  const rootClass = chrome === 'flush'
    ? 'flex flex-col h-full min-h-0 bg-dam-surface-1 overflow-hidden'
    : 'flex flex-col h-full min-h-0 rounded-lg border border-dam-border bg-dam-surface-1 overflow-hidden'

  return (
    <div className={rootClass}>
      <div className="flex items-center gap-1 border-b border-dam-border/50 bg-dam-surface-2 px-2 py-1.5">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`px-2 py-1 rounded text-[10px] font-bold ${tab === key ? 'bg-dam-blue/15 text-dam-blue' : 'text-dam-muted hover:text-dam-text'}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-3">
        {tab === 'summary' && (
          <>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
                <p className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-dam-muted"><AlertTriangle size={11} /> Failure</p>
                <p className="mt-1 text-sm font-bold text-dam-text">{failureLabel ?? (failing.length ? 'Unclassified' : 'None')}</p>
              </div>
              <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
                <p className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-dam-muted"><Shield size={11} /> Failing Guards</p>
                <p className="mt-1 text-sm font-bold text-dam-text">{failing.length}</p>
              </div>
            </div>
            {topReason && (
              <div className="rounded border border-red-500/20 bg-red-500/5 p-2">
                <p className="text-[10px] uppercase tracking-wider text-red-400 font-bold">Primary reason</p>
                <p className="mt-1 max-w-full text-[11px] text-dam-muted leading-relaxed whitespace-pre-wrap break-all">{topReason}</p>
              </div>
            )}
            <FailureLayerBreakdown failing={failing} />
            <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-dam-muted"><Activity size={11} /> Latency</p>
              <div className="mt-2"><LatencySummary latency={latency} totalMs={totalMs} /></div>
            </div>
          </>
        )}
        {tab === 'guards' && (
          <div className="space-y-2">
            {displayGuards.length === 0 ? <p className="text-xs text-dam-muted">No guard results recorded.</p> : displayGuards.map((g, i) => <GuardCard key={`${g.layer}-${g.name}-${i}`} guard={g} displayUnit={unit} />)}
          </div>
        )}
        {tab === 'io' && (
          <div className="space-y-3">
            <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-dam-muted"><Cpu size={11} /> Observation</p>
              <div className="mt-2"><ObservationPanel observation={observation ?? null} displayUnit={unit} /></div>
            </div>
            <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
              <p className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-dam-muted"><Cpu size={11} /> Action</p>
              <div className="mt-2"><ActionPanel action={action ?? null} displayUnit={unit} /></div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
