'use client'
import { Power, StopCircle, Zap, Circle } from 'lucide-react'
import type { RuntimeState, BackendState } from '@/lib/types'

interface Props {
  readonly state: RuntimeState
  readonly cycleCount: number
  readonly error: string | null
  readonly loading: boolean
  readonly connected: boolean
  /** When set, Start and Resume are disabled (hardware not connected). */
  readonly startupError?: string | null
  readonly onStart: () => void
  readonly onStop: () => void
  readonly onEStop: () => void
  readonly onReset: () => void
  readonly backendState: BackendState
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

  const btnBase = 'inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[11px] font-bold uppercase tracking-wide border transition-colors disabled:opacity-35 disabled:cursor-not-allowed'

  return (
    <div className="rounded-lg border border-dam-border/50 bg-dam-surface-2/70 shadow-sm">
      <div className="flex h-12 items-center justify-between gap-3 px-3">

        {/* Action buttons (Center) */}
        <div className="flex items-center gap-1.5 shrink-0">
          {!isActive && !isStarting && !isStopping ? (
            <button
              onClick={onStart}
              disabled={loading || hwBlocked || !canStart}
              title="Start the configured runtime"
              className={`${btnBase} bg-dam-surface-1 text-dam-green border-dam-border/70 hover:bg-dam-green/10 hover:border-dam-green/40`}
            >
              <Power size={12} /> {backendState === 'faulted' ? 'FAULTED' : 'START'}
            </button>
          ) : isStarting ? (
            <button
              disabled
              className={`${btnBase} bg-dam-blue/10 text-dam-blue border-dam-blue/30 opacity-100`}
            >
              <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> STARTING
            </button>
          ) : isStopping ? (
            <button
              disabled
              className={`${btnBase} bg-dam-orange/10 text-dam-orange border-dam-orange/30 opacity-100`}
            >
               <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> STOPPING
            </button>
          ) : (
            <button
              onClick={onStop}
              disabled={loading}
              title="Gracefully stop after the current cycle completes"
              className={`${btnBase} bg-dam-surface-1 text-dam-orange border-dam-border/70 hover:bg-dam-orange/10 hover:border-dam-orange/40`}
            >
              <StopCircle size={12} /> STOP
            </button>
          )}

          {/* E-STOP: only shown while runtime is active; unlike STOP, this disconnects the runner. */}
          {(isActive || isStarting || isStopping) && (
            <button
              onClick={onEStop}
              title="Emergency stop: immediately shuts down and disconnects hardware"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border px-3.5 text-[11px] font-black uppercase tracking-wide transition-colors
                bg-red-500/5 text-red-300 border-red-500/30
                hover:bg-red-500/12 hover:border-red-400/50"
            >
              <Zap size={13} strokeWidth={3} /> E-STOP
            </button>
          )}
        </div>

        {/* Cycle counter*/}
        <div className="flex items-center gap-3 shrink-0">
          {connected && (
            <div className="flex h-8 items-center rounded-md border border-dam-border/60 bg-dam-surface-1 px-2.5">
               <div className="flex items-baseline gap-1.5">
                 <span className="text-[12px] text-dam-text font-mono tracking-tight font-black">
                   {cycleCount > 999999
                     ? (cycleCount / 1000000).toFixed(2) + 'M'
                     : cycleCount > 999
                       ? (cycleCount / 1000).toFixed(1) + 'K'
                       : cycleCount.toLocaleString()}
                 </span>
                 <span className="text-dam-muted/60 text-[8px] uppercase font-black tracking-widest">checks</span>
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
