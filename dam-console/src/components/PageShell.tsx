'use client'
import React from 'react'
import { ControlBar } from '@/components/ControlBar'
import { useRuntimeControl } from '@/hooks/useRuntimeControl'
import { useTelemetry } from '@/hooks/useTelemetry'

interface PageShellProps {
  title: string
  subtitle: string
  children: React.ReactNode
}

export function PageShell({ title, subtitle, children }: PageShellProps) {
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
  
  // Reset telemetry on system idle to ensure fresh state for next run
  React.useEffect(() => {
    if (status.state === 'idle') {
      tele.reset()
      tele.reconnect()
    }
  }, [status.state, tele])

  return (
    <div className="p-5 space-y-5 min-h-screen max-w-7xl mx-auto">
      {/* Header with Integrated Control */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-2">
        <div className="animate-in slide-in-from-left-4 duration-700">
          <h1 className="text-xl font-black text-dam-text tracking-tight uppercase italic">{title}</h1>
          <p className="text-dam-muted text-[10px] font-bold uppercase tracking-widest mt-0.5 opacity-70">{subtitle}</p>
        </div>
        
        {/* The shared control bar */}
        <div className="w-full md:w-auto animate-in slide-in-from-right-4 duration-700">
          <ControlBar
            state={status.state}
            cycleCount={tele.totalCycles}
            error={error}
            loading={loading}
            connected={tele.connected}
            startupError={status.startup_error}
            onStart={start}
            onStop={stop}
            onEStop={emergencyStop}
          />
        </div>
      </div>

      {/* Main Content Area */}
      <main className="animate-in fade-in slide-in-from-bottom-2 duration-700">
        {children}
      </main>
    </div>
  )
}
