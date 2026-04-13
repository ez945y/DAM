'use client'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid } from 'recharts'

interface CustomTooltipProps {
  active?: boolean
  payload?: Array<{ value: number }>
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2 text-xs shadow-xl">
      <span className="text-dam-blue font-mono font-bold">{payload[0].value.toFixed(1)} ms</span>
    </div>
  )
}

export function LatencyChart({ data }: { data: number[] }) {
  // If no data, show a flat baseline of 60 points
  const displayData = data.length === 0 ? new Array(60).fill(0) : data
  // Pad small datasets to ensure chart fills the width
  const paddedData = displayData.length < 60 && data.length > 0 
    ? [...new Array(60 - displayData.length).fill(0), ...displayData]
    : displayData

  const chartData = paddedData.map((v, i) => ({ i, ms: parseFloat(v.toFixed(2)) }))
  const avg  = data.length ? (data.reduce((a, b) => a + b, 0) / data.length) : 0
  const max  = data.length ? Math.max(...data) : 0
  const last = data.length ? data[data.length - 1] : 0

  return (
    <div className="space-y-3">
      {/* Micro stats row */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: 'Last',    value: last.toFixed(1), unit: 'ms' },
          { label: 'Average', value: avg.toFixed(1),  unit: 'ms', accent: true },
          { label: 'Peak',    value: max.toFixed(1),  unit: 'ms' },
        ].map(s => (
          <div key={s.label} className="bg-dam-surface-2 rounded-xl border border-dam-border px-3 py-2 text-center">
            <p className="section-label mb-0.5">{s.label}</p>
            <p className={`metric-value text-sm ${s.accent ? 'text-dam-blue' : 'text-dam-text'}`}>
              {s.value}<span className="text-dam-muted text-[10px] ml-0.5">{s.unit}</span>
            </p>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div style={{ height: 100 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
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
              domain={['auto', 'auto']}
              allowDataOverflow={false}
            />
            <Tooltip content={<CustomTooltip />} />
            {avg > 0 && (
              <ReferenceLine
                y={avg}
                stroke="#3B82F6"
                strokeDasharray="4 3"
                strokeWidth={1}
                strokeOpacity={0.4}
              />
            )}
            {/* The vertical start line */}
            <ReferenceLine x={0} stroke="#ffffff15" strokeWidth={1} />
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
    </div>
  )
}
