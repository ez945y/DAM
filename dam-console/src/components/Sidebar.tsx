'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard, AlertTriangle, Settings,
  Zap, Activity, Circle, ShieldCheck, Film, RotateCcw
} from 'lucide-react'
import { useRuntimeControl } from '@/hooks/useRuntimeControl'
import type { BackendState } from '@/lib/types'

const BACKEND_STYLE: Record<BackendState, { text: string; label: string; dot: string }> = {
  loading:  { text: 'text-yellow-500', label: 'INITIALIZING', dot: 'bg-yellow-500 animate-pulse' },
  ready:    { text: 'text-dam-green',  label: 'SYS READY',    dot: 'bg-dam-green shadow-[0_0_8px_#10b981]' },
  error:    { text: 'text-dam-red',    label: 'SYS ERROR',    dot: 'bg-dam-red animate-ping' },
  faulted:  { text: 'text-dam-red',    label: 'SAFETY FAULT', dot: 'bg-dam-red animate-pulse' },
}

const NAV = [
  { href: '/',             label: 'Dashboard',    icon: LayoutDashboard, section: 'Monitor' },
  { href: '/risk-log',     label: 'Risk Log',     icon: AlertTriangle,   section: 'Monitor' },
  { href: '/mcap-viewer',  label: 'MCAP Sessions',icon: Film,            section: 'Monitor' },
  { href: '/config',       label: 'Config',       icon: Settings,        section: 'Setup'   },
  { href: '/guard',        label: 'Guard',        icon: ShieldCheck,     section: 'Setup'   },
]

export function Sidebar() {
  const path = usePathname()
  const { status, confirmFault, recheckHardware, loading } = useRuntimeControl()
  const bs = status.backend_state
  const sc = BACKEND_STYLE[bs] || BACKEND_STYLE.loading

  return (
    <nav className="w-[200px] shrink-0 bg-dam-surface border-r border-dam-border flex flex-col select-none">
      {/* Branding */}
      <div className="px-4 pt-5 pb-4 border-b border-dam-border">
        <div className="flex items-center gap-2.5 mb-0.5">
          <div className="w-7 h-7 rounded-lg bg-dam-blue flex items-center justify-center shrink-0">
            <Zap size={14} className="text-black" strokeWidth={2.5} />
          </div>
          <div>
            <p className="text-sm font-black text-dam-text tracking-widest leading-none">DAM</p>
            <p className="text-[9px] text-dam-muted tracking-[0.15em] uppercase leading-none mt-0.5">Console</p>
          </div>
        </div>
      </div>

      {/* Nav items */}
      <div className="flex-1 py-3 space-y-0.5 px-2">
        {NAV.map(({ href, label, icon: Icon, section }, i) => {
          const active = path === href
          const prevSection = i > 0 ? NAV[i - 1].section : null
          const showSection = section && section !== prevSection

          return (
            <div key={href}>
              {showSection && (
                <p className="px-2 pt-3 pb-1 text-[9px] uppercase tracking-[0.15em] text-dam-muted/50 font-semibold">
                  {section}
                </p>
              )}
              <Link
                href={href}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150 ${
                  active
                    ? 'bg-dam-blue/10 text-dam-blue border border-dam-blue/20 shadow-[inset_0_1px_0_rgba(59,130,246,0.1)]'
                    : 'text-dam-muted hover:text-dam-text hover:bg-white/[0.03] border border-transparent'
                }`}
              >
                <Icon size={14} className={active ? 'text-dam-blue' : ''} />
                {label}
              </Link>
            </div>
          )
        })}
      </div>

      {/* Bottom status panel */}
      <div className="p-3 border-t border-dam-border space-y-2">
        {/* Dynamic Action Button (Confirm or Recheck) */}
        {(bs === 'error' || bs === 'faulted') && (
          <button
            onClick={bs === 'faulted' ? confirmFault : recheckHardware}
            disabled={loading}
            className={`w-full flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-[10px] font-black uppercase tracking-widest border transition-all ${
              bs === 'faulted'
                ? 'bg-dam-red/20 text-dam-red border-dam-red/40 hover:bg-dam-red/30 animate-pulse'
                : 'bg-dam-orange/20 text-dam-orange border-dam-orange/40 hover:bg-dam-orange/30'
            }`}
          >
            {bs === 'faulted' ? <ShieldCheck size={12} /> : <RotateCcw size={12} />}
            {bs === 'faulted' ? 'Confirm Safety' : 'Recheck HW'}
          </button>
        )}

        <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-dam-surface-2 border border-dam-border">
          <Activity size={11} className="text-dam-muted" />
          <span className="text-[10px] text-dam-muted font-mono">v0.3.0</span>
          <span className={`ml-auto flex items-center gap-1 text-[10px] font-bold ${sc.text}`}>
            <Circle size={5} className={`fill-current ${sc.dot}`} />
            {sc.label}
          </span>
        </div>
      </div>
    </nav>
  )
}
