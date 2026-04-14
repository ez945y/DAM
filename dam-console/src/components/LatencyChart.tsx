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
  policy:  '#F59E0B',   // amber
  guards:  '#10B981',   // emerald
  sink:    '#3B82F6',   // blue
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

  const layerKeys  = Object.keys(perf.layers ?? {}).sort()
  const maxLayerMs = layerKeys.reduce((m, k) => Math.max(m, perf.layers[k] ?? 0), 0.001)

  return (
    <div className="space-y-3">
      {/* ── Current cycle pipeline stages ── */}
      <div className="space-y-1.5">
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
        <div className="pt-2 border-t border-dam-border/30 space-y-1.5">
          <span className="text-[10px] text-dam-muted uppercase tracking-wider">Guard layers</span>
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
}: {
  data: number[]
  cycleIds?: number[]
  onCycleClick?: (cycleId: number) => void
}) {
  const chartData = data.map((v, i) => ({ i, ms: parseFloat(v.toFixed(2)) }))
  const avg  = data.length ? data.reduce((a, b) => a + b, 0) / data.length : 0
  const max  = data.length ? Math.max(...data) : 0
  const last = data.length ? data[data.length - 1] : 0

  const handleClick = (e: { activeTooltipIndex?: number }) => {
    if (!onCycleClick || !cycleIds) return
    const idx = e?.activeTooltipIndex
    if (idx !== undefined && cycleIds[idx] !== undefined) {
      onCycleClick(cycleIds[idx])
    }
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: 'Last',    value: last.toFixed(1), accent: false },
          { label: 'Average', value: avg.toFixed(1),  accent: true  },
          { label: 'Peak',    value: max.toFixed(1),  accent: false },
        ].map(s => (
          <div key={s.label} className="bg-dam-surface-2 rounded-xl border border-dam-border px-3 py-2 text-center">
            <p className="section-label mb-0.5">{s.label}</p>
            <p className={`metric-value text-sm ${s.accent ? 'text-dam-blue' : 'text-dam-text'}`}>
              {s.value}<span className="text-dam-muted text-[10px] ml-0.5">ms</span>
            </p>
          </div>
        ))}
      </div>

      <div style={{ height: 72 }} className={onCycleClick ? 'cursor-pointer' : ''}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={chartData}
            margin={{ top: 4, right: 4, left: -24, bottom: 0 }}
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
              domain={[0, 'auto']}
            />
            <Tooltip content={<HistoryTooltip />} />
            {avg > 0 && (
              <ReferenceLine y={avg} stroke="#3B82F6" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.4} />
            )}
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
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {onCycleClick && data.length > 0 && (
        <p className="text-[9px] text-dam-muted/50 text-center tracking-wide">
          Click a point to view in Risk Log
        </p>
      )}
    </div>
  )
}

// ── Public component ──────────────────────────────────────────────────────

interface LatencyChartProps {
  data: number[]
  perf?: PerfSnapshot | null
  cycleIds?: number[]
  onCycleClick?: (cycleId: number) => void
}

export function LatencyChart({ data, perf, cycleIds, onCycleClick }: LatencyChartProps) {
  return (
    <div className="space-y-4">
      {perf
        ? <LivePipelineBar perf={perf} />
        : (
          <div className="flex items-center justify-center h-12 rounded-lg border border-dashed border-dam-border/40 text-[10px] text-dam-muted/50 tracking-wider uppercase">
            Waiting for pipeline data…
          </div>
        )
      }

      <div className="border-t border-dam-border/30 pt-3">
        <p className="section-label mb-2">History (last {Math.min(data.length, 60)} cycles)</p>
        <RollingHistoryChart data={data} cycleIds={cycleIds} onCycleClick={onCycleClick} />
      </div>
    </div>
  )
}
