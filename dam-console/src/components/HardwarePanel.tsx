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
  const displayValue = value == null ? '—' : value.toFixed(1)
  const warn = warnAt != null && value != null && value >= warnAt
  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2.5 space-y-1.5 min-w-0">
      <div className="flex items-center gap-1.5">
        <Icon size={12} className="text-dam-muted" />
        <span className="text-[10px] text-dam-muted uppercase tracking-wider">{label}</span>
      </div>
      <div className={`metric-value text-base font-mono ${warn ? 'text-amber-400' : 'text-dam-text'}`}>
        {displayValue}
        <span className="text-[10px] text-dam-muted ml-1">{unit}</span>
      </div>
      {bar && <Bar value={value == null ? 0 : Math.max(0, Math.min(100, value))} warn={warn} />}
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
  const hasMotorData = !!(hardware?.temperatures || hardware?.currents || hardware?.voltages)
  const hasData = !!hh || hasMotorData

  return (
    <div className={`space-y-3 transition-opacity ${hasData && taskLive ? '' : 'opacity-60'}`}>
      {!taskLive && (
        <div className="text-[9px] uppercase tracking-widest text-dam-muted/60">
          {hasData ? 'Stopped — last seen' : 'Hardware idle'}
        </div>
      )}
      {taskLive && !hasData && (
        <div className="text-[9px] uppercase tracking-widest text-dam-muted/60">
          No hardware telemetry
        </div>
      )}
      <HostHealthTiles hh={hh ?? {}} />
      <div className="bg-dam-surface-1 border border-dam-border/50 rounded-lg p-3">
        <div className="flex items-center gap-1.5 mb-2">
          <HardDrive size={12} className="text-dam-muted" />
          <span className="text-[10px] text-dam-muted uppercase tracking-wider">Motor readings</span>
        </div>
        {hasMotorData ? (
          <HardwareReadingsTable
            temperatures={hardware?.temperatures}
            currents={hardware?.currents}
            voltages={hardware?.voltages}
          />
        ) : (
          <HardwareReadingsTable temperatures={{ J1: null }} currents={{ J1: null }} voltages={{ J1: null }} />
        )}
      </div>
    </div>
  )
}
