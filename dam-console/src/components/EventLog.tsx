'use client'
import { useState } from 'react'
import { AlertOctagon, ShieldX, ShieldAlert, Info, Filter } from 'lucide-react'
import type { LogEntry, GuardDecision } from '@/lib/types'

type LogType = GuardDecision | 'INFO' | 'all'

const TYPE_CONFIG: Record<LogType, { color: string; dot: string; Icon: React.ComponentType<{ size?: number | string; className?: string }> }> = {
  all:    { color: 'text-dam-text',   dot: 'bg-dam-muted',   Icon: Filter       },
  INFO:   { color: 'text-dam-muted',  dot: 'bg-dam-muted',   Icon: Info         },
  PASS:   { color: 'text-dam-muted',  dot: 'bg-dam-green',   Icon: Info         },
  CLAMP:  { color: 'text-dam-blue', dot: 'bg-dam-blue',  Icon: ShieldAlert  },
  REJECT: { color: 'text-dam-orange', dot: 'bg-dam-orange',  Icon: ShieldX      },
  FAULT:  { color: 'text-dam-red',    dot: 'bg-dam-red',     Icon: AlertOctagon },
}

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function EventLog({ entries }: { entries: LogEntry[] }) {
  const [filter, setFilter] = useState<LogType>('all')

  // Normalize entries to uppercase for consistent filtering and counting
  const normalizedEntries = entries.map(e => ({
    ...e,
    type: e.type.toUpperCase() as LogType
  }))

  const filtered = filter === 'all' ? normalizedEntries : normalizedEntries.filter(e => e.type === filter)

  const counts: Record<string, number> = {}
  for (const e of normalizedEntries) {
    counts[e.type] = (counts[e.type] ?? 0) + 1
  }

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Filter pills */}
      <div className="flex items-center gap-1.5 flex-wrap shrink-0">
        {(['all', 'REJECT', 'FAULT', 'CLAMP', 'INFO'] as LogType[]).map(t => {
          const cnt = t === 'all' ? entries.length : (counts[t] ?? 0)
          const active = filter === t
          return (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold uppercase tracking-wide transition-all border ${
                active
                  ? 'bg-dam-blue text-white border-dam-blue shadow-[0_0_8px_rgba(59,130,246,0.3)]'
                  : 'bg-dam-surface-2 text-dam-muted border-dam-border hover:border-dam-blue/30 hover:text-dam-text'
              }`}
            >
              {t}
              {cnt > 0 && <span className={`px-1 rounded text-[9px] font-bold ${active ? 'bg-black/20' : 'bg-dam-surface-3'}`}>{cnt}</span>}
            </button>
          )
        })}
      </div>

      {/* Log entries */}
      <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-dam-muted gap-1.5 py-6">
            <Filter size={20} className="opacity-20" />
            <p className="text-xs">No events</p>
          </div>
        ) : (
          filtered.map((e, i) => {
            const upCaseType = e.type.toUpperCase() as LogType
            const cfg = TYPE_CONFIG[upCaseType] ?? TYPE_CONFIG.INFO
            const { Icon } = cfg
            return (
              <div key={i} className={`flex items-start gap-2 py-1.5 px-2 rounded-lg hover:bg-white/[0.02] ${cfg.color}`}>
                <span className="shrink-0 text-[10px] font-mono text-dam-muted mt-0.5 tabular-nums">{fmtTime(e.timestamp)}</span>
                <Icon size={11} className={`shrink-0 mt-0.5 ${cfg.color}`} />
                <span className="text-[11px] leading-snug break-all">{e.message}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
