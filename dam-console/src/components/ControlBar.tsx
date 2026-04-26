'use client'
import { Power, StopCircle, Zap, Circle } from 'lucide-react'
import type { RuntimeState, BackendState } from '@/lib/types'

interface Props {
  state: RuntimeState
  cycleCount: number
  error: string | null
  loading: boolean
  connected: boolean
  /** When set, Start and Resume are disabled (hardware not connected). */
  startupError?: string | null
  onStart: () => void
  onStop: () => void
  onEStop: () => void
  onReset: () => void
  backendState: BackendState
}

const STATE_CONFIG: Record<RuntimeState, { label: string; dot: string; bg: string; text: string; border: string }> = {
  idle:      { label: 'READY',     dot: 'bg-dam-muted',   bg: 'bg-white/5',        text: 'text-dam-muted', border: 'border-white/10' },
  starting:  { label: 'BUSY',      dot: 'bg-yellow-500',  bg: 'bg-yellow-500/10',  text: 'text-yellow-500', border: 'border-yellow-500/30' },
  running:   { label: 'ACTIVE',    dot: 'bg-dam-green',   bg: 'bg-dam-green/10',   text: 'text-dam-green', border: 'border-dam-green/30' },
  paused:    { label: 'PAUSED',    dot: 'bg-dam-blue',    bg: 'bg-dam-blue/10',    text: 'text-dam-blue',  border: 'border-dam-blue/30' },
  stopping:  { label: 'STOPPING',  dot: 'bg-dam-orange',  bg: 'bg-dam-orange/10',  text: 'text-dam-orange', border: 'border-dam-orange/30' },
  stopped:   { label: 'STOPPED',   dot: 'bg-dam-orange',  bg: 'bg-dam-orange/10',  text: 'text-dam-orange', border: 'border-dam-orange/30' },
  emergency: { label: 'FAULT',     dot: 'bg-dam-red',     bg: 'bg-dam-red/10',     text: 'text-dam-red',   border: 'border-dam-red/30' },
}

export function ControlBar({ state, backendState, cycleCount, error, loading, connected, startupError, onStart, onStop, onEStop, onReset }: Props) {
  const isActive = state === 'running' || state === 'paused'
  const isStarting = state === 'starting'
  const isStopping = state === 'stopping'
  const systemReady = backendState === 'ready'
  const canStart = systemReady && (state === 'idle' || state === 'stopped')
  const hwBlocked = !!startupError || !systemReady

  const btnBase = 'flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-bold border transition-all disabled:opacity-30 disabled:cursor-not-allowed'

  return (
    <div className="panel p-0 bg-dam-surface-2/50 backdrop-blur-md border-dam-border/40">
      <div className="flex items-center justify-between gap-4 px-4 h-12">

        {/* Action buttons (Center) */}
        <div className="flex items-center gap-2 shrink-0">
          {!isActive && !isStarting && !isStopping ? (
            <button
              onClick={onStart}
              disabled={loading || hwBlocked || !canStart}
              className={`${btnBase} bg-dam-green/10 text-dam-green border-dam-green/40 hover:bg-dam-green/20 hover:border-dam-green active:scale-95`}
            >
              <Power size={12} /> {backendState === 'faulted' ? 'FAULTED' : 'START'}
            </button>
          ) : isStarting ? (
            <button
              disabled
              className={`${btnBase} bg-dam-blue/10 text-dam-blue border-dam-blue/40 opacity-100`}
            >
              <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> STARTING
            </button>
          ) : isStopping ? (
            <button
              disabled
              className={`${btnBase} bg-dam-orange/10 text-dam-orange border-dam-orange/40 opacity-100`}
            >
               <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> STOPPING
            </button>
          ) : (
            <button
              onClick={onStop}
              disabled={loading}
              className={`${btnBase} bg-dam-orange/10 text-dam-orange border-dam-orange/40 hover:bg-dam-orange/20 hover:border-dam-orange active:scale-95`}
            >
              <StopCircle size={12} /> STOP
            </button>
          )}

          {/* E-STOP */}
          <button
            onClick={onEStop}
            className="flex items-center gap-2 px-4 py-1.5 rounded text-[11px] font-black border-2 transition-all uppercase tracking-wider
              bg-dam-red/10 text-dam-red border-dam-red/40
              hover:bg-dam-red/20 hover:border-dam-red hover:shadow-[0_0_15px_rgba(239,68,68,0.2)]
              active:scale-95 group"
          >
            <Zap size={13} strokeWidth={3} className="group-hover:animate-pulse" /> E-STOP
          </button>
        </div>

        {/* Cycle counter*/}
        <div className="flex items-center gap-3 shrink-0">
          {connected && (
            <div className="flex flex-col items-end -space-y-0.5 animate-in fade-in slide-in-from-right-2">
               <div className="flex items-baseline gap-1">
                 <span className="text-[12px] text-dam-text/90 font-mono tracking-tight font-black">
                   {cycleCount > 999999
                     ? (cycleCount / 1000000).toFixed(2) + 'M'
                     : cycleCount > 999
                       ? (cycleCount / 1000).toFixed(1) + 'K'
                       : cycleCount.toLocaleString()}
                 </span>
                 <span className="text-white/20 text-[8px] uppercase font-black tracking-widest">checks</span>
               </div>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="px-4 pb-2 text-dam-red text-[10px] font-medium border-t border-dam-red/10 pt-1">
          <span className="opacity-70">⚠ Runtime Error:</span> {error}
        </div>
      )}
    </div>
  )
}
