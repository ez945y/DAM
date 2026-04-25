'use client'
import { Power, PauseCircle, PlayCircle, StopCircle, Zap, RotateCcw, Circle } from 'lucide-react'
import type { RuntimeState, BackendState } from '@/lib/types'

interface Props {
  state: RuntimeState
  backendState: BackendState
  cycleCount: number
  error: string | null
  loading: boolean
  onStart: () => void
  onPause: () => void
  onResume: () => void
  onStop: () => void
  onEStop: () => void
  onReset: () => void
}

const STATE_CONFIG: Record<RuntimeState, { label: string; dot: string; bg: string; text: string }> = {
  idle:      { label: 'IDLE',      dot: 'bg-dam-muted',   bg: 'bg-dam-surface-3',  text: 'text-dam-muted'   },
  starting:  { label: 'STARTING',  dot: 'bg-yellow-500',  bg: 'bg-yellow-500/10',  text: 'text-yellow-500'  },
  running:   { label: 'RUNNING',   dot: 'bg-dam-green',   bg: 'bg-[#071a0e]',      text: 'text-dam-green'   },
  paused:    { label: 'PAUSED',    dot: 'bg-dam-blue',    bg: 'bg-dam-blue-dim',   text: 'text-dam-blue'    },
  stopping:  { label: 'STOPPING',  dot: 'bg-orange-500',  bg: 'bg-orange-500/10',  text: 'text-orange-500'  },
  stopped:   { label: 'STOPPED',   dot: 'bg-dam-orange',  bg: 'bg-[#1a0d00]',      text: 'text-dam-orange'  },
  emergency: { label: 'EMERGENCY', dot: 'bg-dam-red',     bg: 'bg-[#1a0505]',      text: 'text-dam-red'     },
}

export function ControlPanel({ state, backendState, cycleCount, error, loading, onStart, onPause, onResume, onStop, onEStop, onReset }: Props) {
  const sc = STATE_CONFIG[state]
  const isRunning = state === 'running'
  const isPaused  = state === 'paused'
  const isStarting = state === 'starting'
  const isStopping = state === 'stopping'
  const isEmergency = state === 'emergency'

  const systemReady = backendState === 'ready'
  const isActive = isRunning || isPaused || isStarting || isStopping
  const canStart = systemReady && (state === 'idle' || state === 'stopped')

  return (
    <div className="panel p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="section-label">Runtime Control</p>
        <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border ${backendState === 'faulted' ? 'bg-dam-red/10 text-dam-red border-dam-red/30' : `${sc.bg} ${sc.text} ${sc.text.replace('text-', 'border-').replace('dam-', '')}/30`}`}>
          <Circle
            size={5}
            className={`fill-current ${backendState === 'faulted' ? 'bg-dam-red animate-pulse' : `${sc.dot} ${state === 'running' ? 'animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.4)]' : ''} ${state === 'emergency' ? 'animate-ping' : ''} ${(isStarting || isStopping) ? 'animate-pulse' : ''}`}`}
          />
          {backendState === 'faulted' ? 'SYSTEM HALTED' : sc.label}
        </div>
      </div>

      {/* Cycle counter */}
      <div className="flex items-baseline gap-2 px-3 py-2 bg-dam-surface-2 rounded-xl border border-dam-border">
        <span className="metric-value text-lg text-dam-text">{cycleCount.toLocaleString()}</span>
        <span className="text-dam-muted text-xs">cycles executed</span>
      </div>

      {/* Primary controls */}
      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={onStart}
          disabled={!canStart || loading}
          className="group flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold border transition-all
            bg-green-950/60 text-dam-green border-green-900
            hover:bg-green-900/60 hover:border-green-700 hover:shadow-[0_0_12px_rgba(34,197,94,0.2)]
            disabled:opacity-40 disabled:bg-dam-surface-2 disabled:border-dam-border disabled:text-dam-muted disabled:cursor-not-allowed"
        >
          {isStarting ? <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> : <Power size={12} />}
          {isStarting ? 'Starting Loop...' : isEmergency ? 'Loop Halted' : 'Start'}
        </button>

        <button
          onClick={isPaused ? onResume : onPause}
          disabled={!isActive || loading}
          className="group flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold border transition-all
            bg-blue-950/60 text-dam-blue border-blue-900
            hover:bg-blue-900/60 hover:border-blue-700 hover:shadow-[0_0_12px_rgba(59,130,246,0.2)]
            disabled:opacity-30 disabled:cursor-not-allowed"
        >
          {isPaused
            ? <><PlayCircle size={12} /> Resume</>
            : <><PauseCircle size={12} /> Pause</>}
        </button>

        <button
          onClick={onStop}
          disabled={!isActive || isStopping || loading}
          className="flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold border transition-all
            bg-orange-950/60 text-dam-orange border-orange-900
            hover:bg-orange-900/60 hover:border-orange-700
            disabled:opacity-30 disabled:cursor-not-allowed"
        >
          {isStopping ? <Circle size={12} className="animate-spin border-2 border-t-transparent rounded-full" /> : <StopCircle size={12} />}
          {isStopping ? 'Stopping...' : 'Stop'}
        </button>

        <button
          onClick={onReset}
          disabled={isActive || loading}
          className="flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold border transition-all
            bg-dam-surface-2 text-dam-muted border-dam-border
            hover:text-dam-text hover:bg-dam-surface-3
            disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <RotateCcw size={12} /> Reset
        </button>
      </div>

      {/* E-STOP */}
      <button
        onClick={onEStop}
        disabled={loading}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-black border-2 transition-all uppercase tracking-widest
          bg-red-950/70 text-dam-red border-red-900
          hover:bg-red-900/80 hover:border-red-700 hover:shadow-[0_0_20px_rgba(239,68,68,0.3)]
          disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <Zap size={14} strokeWidth={2.5} /> Emergency Stop
      </button>

      {error && (
        <div className="flex gap-2 items-start bg-red-950/40 border border-red-900/60 rounded-lg px-3 py-2">
          <span className="text-dam-red text-xs mt-0.5">⚠</span>
          <p className="text-dam-red text-xs break-all">{error}</p>
        </div>
      )}
    </div>
  )
}
