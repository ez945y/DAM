'use client'
import {
  AreaChart, Area,
  XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
} from 'recharts'
import type { PerfSnapshot } from '@/lib/types'

// ── Colour palette ────────────────────────────────────────────────────────
const STAGE_COLORS: Record<string, string> = {
  source:  '#6366F1',   // indigo
  context: '#A855F7',   // purple
  policy:  '#F59E0B',   // amber
  guards:  '#10B981',   // emerald
  sink:    '#3B82F6',   // blue
}
const STAGE_LABELS: Record<string, string> = {
  source: 'Source', context: 'Context', policy: 'Policy', guards: 'Guards', sink: 'Sink',
}
const STAGE_ORDER = ['source', 'context', 'policy', 'guards', 'sink'] as const

const EMPTY_PERF: PerfSnapshot = {
  stages: { source: 0, context: 0, policy: 0, guards: 0, sink: 0, total: 0 },
  layers: { L1: 0, L3: 0 },
  guards: {},
  deadline_ms: 0,
  slack_ms: 0,
}

// Mirrors dam.guard.layer.GuardLayer
const LAYER_META: Record<string, { label: string; color: string }> = {
  L0: { label: 'L0 OOD',        color: '#A78BFA' },
  L1: { label: 'L1 Kinematics', color: '#34D399' },
  L2: { label: 'L2 Task',       color: '#10B981' },
  L3: { label: 'L3 Hardware',   color: '#6EE7B7' },
}

// ── Compact CSS bar row (shared by pipeline stages and guard layers) ─────────
function BarRow({
  label, ms, maxMs, color, pct,
}: {
  label: string; ms: number; maxMs: number; color: string; pct?: number
}) {
  const barW = maxMs > 0 ? (ms / maxMs) * 100 : 0
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-dam-muted shrink-0 w-20 truncate">{label}</span>
      <div className="flex-1 h-2 bg-dam-surface-1 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${barW}%`, background: color }}
        />
      </div>
      <span className="font-mono text-[10px] text-dam-text w-12 text-right shrink-0">
        {ms.toFixed(1)} ms
      </span>
      {pct !== undefined && (
        <span className="font-mono text-[9px] text-dam-muted w-7 text-right shrink-0">
          {pct.toFixed(0)}%
        </span>
      )}
    </div>
  )
}

// ── Live pipeline breakdown ───────────────────────────────────────────────
function LivePipelineBar({ perf }: { perf: PerfSnapshot }) {
  const stages  = perf.stages
  const total   = stages['total'] ?? 0
  const maxStageMs = Math.max(...STAGE_ORDER.map(s => stages[s] ?? 0), 0.001)

  const layerKeys  = Object.keys(perf.layers ?? {}).sort((a, b) => a.localeCompare(b))
  const maxLayerMs = layerKeys.reduce((m, k) => Math.max(m, perf.layers[k] ?? 0), 0.001)

  return (
    // Two columns side by side to save vertical space: pipeline stages
    // on the left, guard-layer breakdown on the right.
    <div className={`grid gap-x-5 gap-y-2 ${layerKeys.length > 0 ? 'grid-cols-2' : 'grid-cols-1'}`}>
      {/* ── Current cycle pipeline stages ── */}
      <div className="space-y-1.5 min-w-0">
        <div className="flex items-baseline justify-between mb-0.5">
          <span className="text-[10px] text-dam-muted uppercase tracking-wider">Current cycle</span>
          <span className="font-mono text-sm font-bold text-dam-blue">{total.toFixed(1)} ms</span>
        </div>
        {STAGE_ORDER.map(s => (
          <BarRow
            key={s}
            label={STAGE_LABELS[s]}
            ms={stages[s] ?? 0}
            maxMs={maxStageMs}
            color={STAGE_COLORS[s]}
            pct={total > 0 ? ((stages[s] ?? 0) / total) * 100 : 0}
          />
        ))}
      </div>

      {/* ── Guard layers (same CSS bar style) ── */}
      {layerKeys.length > 0 && (
        <div className="space-y-1.5 min-w-0">
          <span className="text-[10px] text-dam-muted uppercase tracking-wider block mb-0.5">Guard layers</span>
          {layerKeys.map(k => (
            <BarRow
              key={k}
              label={LAYER_META[k]?.label ?? k}
              ms={perf.layers[k] ?? 0}
              maxMs={maxLayerMs}
              color={LAYER_META[k]?.color ?? '#6B6B6B'}
              pct={total > 0 ? ((perf.layers[k] ?? 0) / total) * 100 : 0}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Rolling total-latency area chart ──────────────────────────────────────
function HistoryTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number }> }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2 text-xs shadow-xl">
      <span className="text-dam-blue font-mono font-bold">{payload[0].value.toFixed(1)} ms</span>
    </div>
  )
}

function RollingHistoryChart({
  data,
  cycleIds,
  onCycleClick,
  empty = false,
}: {
  data: number[]
  cycleIds?: number[]
  onCycleClick?: (cycleId: number) => void
  empty?: boolean
}) {
  const visibleData = data.length > 0 ? data : [0, 0, 0, 0, 0, 0]
  const chartData = visibleData.map((v, i) => ({ i, ms: Number.parseFloat(v.toFixed(2)) }))
  const avg  = data.length ? data.reduce((a, b) => a + b, 0) / data.length : 0
  const max  = data.length ? Math.max(...data) : 0
  const last = data.at(-1) ?? 0

  const handleClick = (e: { activeTooltipIndex?: number }) => {
    if (!onCycleClick || !cycleIds) return
    const idx = e?.activeTooltipIndex
    if (idx !== undefined && cycleIds[idx] !== undefined) {
      onCycleClick(cycleIds[idx])
    }
  }

  return (
    <div className="space-y-1.5">
      <div style={{ height: 82 }} className={`relative ${onCycleClick && !empty ? 'cursor-pointer' : ''}`}>
        <div className="pointer-events-none absolute left-1 top-1 z-10 flex items-center gap-1 rounded border border-dam-border/50 bg-dam-surface-2/90 px-1.5 py-0.5 shadow-sm">
          {[
            { label: 'Last', value: last.toFixed(1), accent: false },
            { label: 'Avg',  value: avg.toFixed(1),  accent: true  },
            { label: 'Peak', value: max.toFixed(1),  accent: false },
          ].map((s, idx) => (
            <span key={s.label} className="flex items-baseline gap-0.5">
              {idx > 0 && <span className="mx-0.5 h-2.5 w-px bg-dam-border/70" />}
              <span className="text-[8px] font-bold uppercase tracking-wider text-dam-muted/70">{s.label}</span>
              <span className={`font-mono text-[10px] font-bold ${s.accent ? 'text-dam-blue' : 'text-dam-text'}`}>{s.value}</span>
            </span>
          ))}
          <span className="text-[8px] text-dam-muted/70">ms</span>
        </div>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={chartData}
            margin={{ top: 12, right: 4, left: -24, bottom: 0 }}
            onClick={handleClick}
          >
            <defs>
              <linearGradient id="latGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#3B82F6" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#3B82F6" stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#ffffff08" />
            <XAxis dataKey="i" hide />
            <YAxis
              tick={{ fontSize: 9, fill: '#6B6B6B' }}
              tickLine={false}
              axisLine={{ stroke: '#ffffff10' }}
              domain={empty ? [0, 120] : [0, 'auto']}
              ticks={empty ? [60, 120] : undefined}
            />
            <Tooltip content={<HistoryTooltip />} />
            {avg > 0 && (
              <ReferenceLine y={avg} stroke="#3B82F6" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.4} />
            )}
            {!empty && (
              <Area
                type="monotone"
                dataKey="ms"
                stroke="#3B82F6"
                strokeWidth={1.5}
                fill="url(#latGrad)"
                isAnimationActive={false}
                dot={false}
                activeDot={{ r: 3, fill: '#3B82F6', strokeWidth: 0 }}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {onCycleClick && (
        <p className={`text-[9px] text-center tracking-wide ${empty ? 'text-dam-muted/30' : 'text-dam-muted/50'}`}>
          Click a point to view in Risk Log
        </p>
      )}
    </div>
  )
}

// ── Public component ──────────────────────────────────────────────────────

interface LatencyChartProps {
  readonly data: number[]
  readonly perf?: PerfSnapshot | null
  readonly cycleIds?: number[]
  readonly onCycleClick?: (cycleId: number) => void
}

export function LatencyChart({ data, perf, cycleIds, onCycleClick }: LatencyChartProps) {
  const hasPerf = perf != null
  const hasHistory = data.length > 0

  return (
    <div className={`space-y-4 transition-opacity ${hasPerf || hasHistory ? '' : 'opacity-60'}`}>
      <LivePipelineBar perf={perf ?? EMPTY_PERF} />

      <div className="border-t border-dam-border/30 pt-3">
        <RollingHistoryChart
          data={data}
          cycleIds={cycleIds}
          onCycleClick={onCycleClick}
          empty={!hasHistory}
        />
      </div>
    </div>
  )
}
