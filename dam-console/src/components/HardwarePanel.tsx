'use client'
import { Cpu, Thermometer, MemoryStick, Server, HardDrive } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { HostHardwareSnapshot, HostHealth } from '@/lib/types'

function pct(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : null
}

function Bar({ value, warn }: { value: number; warn?: boolean }) {
  return (
    <div className="h-1.5 rounded-full bg-dam-surface-2 overflow-hidden">
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${warn ? 'bg-amber-400' : 'bg-dam-blue'}`}
        style={{ width: `${value}%` }}
      />
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  unit,
  bar,
  warnAt,
}: {
  icon: LucideIcon
  label: string
  value: number | null
  unit: string
  bar?: boolean
  warnAt?: number
}) {
  if (value == null) return null
  const warn = warnAt != null && value >= warnAt
  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2.5 space-y-1.5 min-w-0">
      <div className="flex items-center gap-1.5">
        <Icon size={12} className="text-dam-muted" />
        <span className="text-[10px] text-dam-muted uppercase tracking-wider">{label}</span>
      </div>
      <div className={`metric-value text-base font-mono ${warn ? 'text-amber-400' : 'text-dam-text'}`}>
        {value.toFixed(1)}
        <span className="text-[10px] text-dam-muted ml-1">{unit}</span>
      </div>
      {bar && <Bar value={Math.max(0, Math.min(100, value))} warn={warn} />}
    </div>
  )
}

function HostHealthTiles({ hh }: { hh: HostHealth }) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-4 gap-2">
        <Metric icon={Cpu} label="Host CPU" value={pct(hh.cpu_percent)} unit="%" bar warnAt={90} />
        <Metric icon={MemoryStick} label="Host Mem" value={pct(hh.memory_percent)} unit="%" bar warnAt={90} />
        <Metric
          icon={Thermometer}
          label="Host Temp"
          value={typeof hh.temperature_c === 'number' ? hh.temperature_c : null}
          unit="°C"
          warnAt={85}
        />
      </div>
      {hh.gpus?.map((g, i) => (
        <div key={i} className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-4 gap-2 border-t border-dam-border/30 pt-2">
          <Metric icon={Server} label={`GPU${i} Util`} value={pct(g.util_percent)} unit="%" bar warnAt={95} />
          <Metric
            icon={Thermometer}
            label={`GPU${i} Temp`}
            value={typeof g.temperature_c === 'number' ? g.temperature_c : null}
            unit="°C"
            warnAt={85}
          />
          <Metric
            icon={MemoryStick}
            label={`GPU${i} Mem`}
            value={
              typeof g.memory_used_mb === 'number' && typeof g.memory_total_mb === 'number'
                ? (g.memory_used_mb / Math.max(g.memory_total_mb, 1)) * 100
                : null
            }
            unit="%"
            bar
            warnAt={95}
          />
        </div>
      ))}
    </div>
  )
}

function fmt(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  if (typeof v === 'boolean') return String(v)
  return String(v)
}

function prettyKey(key: string): string {
  return key.replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function isHostHealthGuardName(name: string): boolean {
  const normalized = name.toLowerCase()
  return normalized === 'host_health' || normalized === 'host_health_limit'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function asReadings(value: unknown): Record<string, unknown> {
  if (Array.isArray(value)) {
    return Object.fromEntries(value.map((v, i) => [`J${i + 1}`, v]))
  }
  return asRecord(value) ?? {}
}

function jointSort(a: string, b: string): number {
  const ai = Number(a.match(/^J(\d+)$/i)?.[1])
  const bi = Number(b.match(/^J(\d+)$/i)?.[1])
  if (Number.isFinite(ai) && Number.isFinite(bi)) return ai - bi
  if (Number.isFinite(ai)) return -1
  if (Number.isFinite(bi)) return 1
  return a.localeCompare(b)
}

function fmtReading(v: unknown, unit: string): string {
  return v == null ? '—' : `${fmt(v)}${unit}`
}

function HardwareReadingsTable({
  temperatures,
  currents,
  voltages,
}: {
  temperatures?: unknown
  currents?: unknown
  voltages?: unknown
}) {
  const tempMap = asReadings(temperatures)
  const currentMap = asReadings(currents)
  const voltageMap = asReadings(voltages)
  const joints = Array.from(new Set([
    ...Object.keys(tempMap),
    ...Object.keys(currentMap),
    ...Object.keys(voltageMap),
  ])).sort(jointSort)

  if (joints.length === 0) return null

  return (
    <div className="overflow-x-auto rounded-lg border border-dam-border/50 bg-dam-surface-2/40">
      <table className="w-full min-w-[420px] border-collapse text-left">
        <thead className="bg-dam-surface-2/80 text-[10px] uppercase tracking-wider text-dam-muted">
          <tr>
            <th className="w-[84px] px-3 py-2 font-semibold">Joint</th>
            <th className="px-3 py-2 font-semibold">Temp</th>
            <th className="px-3 py-2 font-semibold">Current</th>
            <th className="px-3 py-2 font-semibold">Voltage</th>
          </tr>
        </thead>
        <tbody>
          {joints.map((joint) => (
            <tr key={joint} className="border-t border-dam-border/35">
              <td className="px-3 py-2 font-mono text-[11px] text-dam-muted">{joint}</td>
              <td className="px-3 py-2 font-mono text-[12px] text-dam-text whitespace-nowrap">
                {fmtReading(tempMap[joint], '°C')}
              </td>
              <td className="px-3 py-2 font-mono text-[12px] text-dam-text whitespace-nowrap">
                {fmtReading(currentMap[joint], 'A')}
              </td>
              <td className="px-3 py-2 font-mono text-[12px] text-dam-text whitespace-nowrap">
                {fmtReading(voltageMap[joint], 'V')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-dam-border/35 px-3 py-1.5 text-[9px] uppercase tracking-wider text-dam-muted/60">
        Joint x hardware
      </div>
    </div>
  )
}

function ScalarGrid({ entries }: { entries: Array<readonly [string, unknown]> }) {
  if (entries.length === 0) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center justify-between gap-3 rounded-md border border-dam-border/30 bg-dam-surface-2/60 px-2 py-1.5 min-w-0">
          <span className="text-[10px] text-dam-muted truncate" title={k}>{prettyKey(k)}</span>
          <span className="font-mono text-[11px] text-dam-text/90 truncate text-right" title={fmt(v)}>{fmt(v)}</span>
        </div>
      ))}
    </div>
  )
}

/** Flexible renderer for arbitrary hardware-guard metadata. */
function MetaTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null || typeof value !== 'object') {
    return <span className="font-mono text-[11px] text-dam-text/90 break-all">{fmt(value)}</span>
  }
  const record = asRecord(value)
  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(record ?? {})
  if (entries.length === 0) return <span className="text-[10px] text-dam-muted/50">—</span>

  const readingGroups = record
    ? ['temperatures', 'currents', 'voltages']
        .filter((key) => record[key] != null)
        .map((key) => [key, record[key]] as const)
    : []
  const scalarEntries = entries.filter(([, v]) => v == null || typeof v !== 'object')
  const nestedEntries = entries.filter(([k, v]) =>
    v != null && typeof v === 'object' && !readingGroups.some(([rk]) => rk === k)
  )

  return (
    <div className={depth > 0 ? 'pl-2 border-l border-dam-border/30 space-y-2 min-w-0' : 'space-y-2 min-w-0'}>
      {readingGroups.length > 0 && (
        <HardwareReadingsTable
          temperatures={record?.temperatures}
          currents={record?.currents}
          voltages={record?.voltages}
        />
      )}
      <ScalarGrid entries={scalarEntries} />
      {nestedEntries.map(([k, v]) => (
        <div key={k} className="space-y-1.5 min-w-0">
          <div className="text-[10px] text-dam-muted uppercase tracking-wider truncate" title={k}>
            {prettyKey(k)}
          </div>
          <MetaTree value={v} depth={depth + 1} />
        </div>
      ))}
    </div>
  )
}

/**
 * Built-in host + hardware health panel. Renders typed host_health tiles plus a
 * flexible view of every L3 / hardware guard's metadata (hardware_watchdog
 * temps/currents/voltages, etc.) — whatever the running stack reports. Keeps
 * the last snapshot visible after Stop (dimmed) instead of blanking.
 */
export function HardwarePanel({
  hardware,
  taskLive,
}: {
  hardware?: HostHardwareSnapshot
  taskLive: boolean
}) {
  const hh = hardware?.host_health
  const guards: Record<string, Record<string, unknown>> = Object.fromEntries(
    Object.entries(hardware?.guards ?? {})
      .filter(([name]) => !isHostHealthGuardName(name))
      .map(([name, meta]) => {
        const filtered = Object.fromEntries(
          Object.entries(meta ?? {}).filter(([key]) => key !== 'host_health')
        )
        return [name, filtered]
      })
      .filter(([, meta]) => Object.keys(meta).length > 0)
  )
  const guardNames = Object.keys(guards)
  const hasData = !!hh || guardNames.length > 0

  if (!hasData) {
    return (
      <div className="flex flex-col items-center justify-center h-44 rounded-xl border border-dashed border-dam-border/40 bg-dam-surface-1/50 text-dam-muted/60 gap-1.5">
        <Cpu size={18} className="opacity-20" />
        <span className="text-[10px] tracking-widest uppercase">
          {taskLive ? 'No hardware telemetry' : 'Hardware idle'}
        </span>
        <span className="text-[9px] text-dam-muted/40">
          add an L3 guard (host_health / hardware_watchdog) to the active task
        </span>
      </div>
    )
  }

  return (
    <div className={`space-y-3 transition-opacity ${taskLive ? '' : 'opacity-60'}`}>
      {!taskLive && (
        <div className="text-[9px] uppercase tracking-widest text-dam-muted/60">
          Stopped — last seen
        </div>
      )}
      {hh && <HostHealthTiles hh={hh} />}
      {guardNames.map((name) => (
        <div key={name} className="bg-dam-surface-1 border border-dam-border/50 rounded-lg p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <HardDrive size={12} className="text-dam-muted" />
            <span className="text-[10px] text-dam-muted uppercase tracking-wider">{name}</span>
          </div>
          <MetaTree value={guards[name]} />
        </div>
      ))}
    </div>
  )
}
