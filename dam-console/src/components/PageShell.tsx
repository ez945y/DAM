'use client'
import React from 'react'
import { ControlBar } from '@/components/ControlBar'
import { useRuntimeControl } from '@/hooks/useRuntimeControl'
import { useTelemetry } from '@/hooks/useTelemetry'

interface PageShellProps {
  readonly title: string
  readonly subtitle: string
  readonly children: React.ReactNode
  readonly headerAction?: React.ReactNode
}

export function PageShell({ title, subtitle, children, headerAction }: PageShellProps) {
  const {
    status,
    loading,
    error,
    start,
    stop,
    emergencyStop,
    reset
  } = useRuntimeControl()

  const tele = useTelemetry()

  // Keep stable refs so the idle-reset effect never needs the whole tele object
  // as a dependency (tele is a new object every 500 ms due to the throttle timer).
  const teleResetRef = React.useRef(tele.reset)
  teleResetRef.current = tele.reset

  const prevStateRef = React.useRef(status.state)

  // Reset telemetry only on an actual transition into idle. A page switch
  // remounts PageShell while the backend may already be idle; resetting there
  // causes WS history replay to be counted again as fresh checks.
  React.useEffect(() => {
    const prev = prevStateRef.current
    prevStateRef.current = status.state
    if (status.state === 'idle' && prev !== 'idle') {
      teleResetRef.current()
    }
  }, [status.state])

  return (
    <div className="p-5 space-y-5 min-h-screen max-w-7xl mx-auto">
      {/* Header with Integrated Control */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-2">
        <div className="animate-in slide-in-from-left-4 duration-700">
          <h1 className="text-xl font-black text-dam-text tracking-tight uppercase italic">{title}</h1>
          <p className="text-dam-muted text-[10px] font-bold uppercase tracking-widest mt-0.5 opacity-70">{subtitle}</p>
        </div>

        {/* The shared control bar */}
        <div className="w-full md:w-auto animate-in slide-in-from-right-4 duration-700 flex items-center justify-end gap-2">
          <ControlBar
            state={status.state}
            backendState={status.backend_state}
            cycleCount={tele.totalCycles}
            error={error}
            loading={loading}
            connected={tele.connected}
            startupError={status.startup_error}
            onStart={start}
            onStop={stop}
            onEStop={emergencyStop}
            onReset={reset}
          />
          {headerAction}
        </div>
      </div>

      {/* Main Content Area */}
      <main className="animate-in fade-in slide-in-from-bottom-2 duration-700">
        {children}
      </main>
    </div>
  )
}
