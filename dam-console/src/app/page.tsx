'use client'
import { useState, useEffect, useMemo } from 'react'
import { useRouter }         from 'next/navigation'
import { useTelemetry }      from '@/hooks/useTelemetry'
import { useRuntimeControl } from '@/hooks/useRuntimeControl'
import { useDemoMode }       from '@/hooks/useDemoMode'
import { useLiveMode }       from '@/hooks/useLiveMode'
import { RiskGauge }         from '@/components/RiskGauge'
import { StatsCard }         from '@/components/StatsCard'
import { GuardTable, DEC_CONFIG } from '@/components/GuardTable'
import { LatencyChart }      from '@/components/LatencyChart'
import { HardwarePanel }     from '@/components/HardwarePanel'
import { McapCameraPlayer }  from '@/components/McapCameraPlayer'
import { Shield, TrendingDown, Timer, Loader, AlertTriangle } from 'lucide-react'
import { PageShell } from '@/components/PageShell'

function formatUptime(sec: number): string {
  if (sec <= 0) return '—'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function useAdapterLabel(): string {
  const [label, setLabel] = useState('Dev server')
  useEffect(() => {
    try {
      const raw = localStorage.getItem('dam_config_v1')
      if (raw) {
        const adapter = (JSON.parse(raw) as { adapter?: string }).adapter ?? 'simulation'
        const isLeRobot = adapter === 'lerobot'
        const isRos2 = adapter === 'ros2'
        const adapterLabel = isLeRobot ? 'LeRobot server' : isRos2 ? 'ROS2 server' : 'Dev server'
        setLabel(adapterLabel)
      }
    } catch { /* ignore */ }
  }, [])
  return label
}

/** Warning icon + popover listing missing hardware devices. */
function HardwareWarning({ message }: { message: string }) {
  const [open, setOpen] = useState(false)

  // Backend errors (e.g. FeetechMotorsBus) are pre-formatted with indented
  // sub-lines like:
  //   Missing motor IDs:
  //     - 1 (expected model: 777)
  // The previous code split on \n and treated every line as a bullet, which
  // produced a flat list of "•" prefixes that obliterated the structure.
  // Keep the raw text instead — render in a monospace block with whitespace
  // preserved so indentation / blank-line separators stay readable.
  const cleaned = useMemo(() => {
    const trimmed = message.replace(/^\s*Emergency Stop Triggered\s*$/m, '').trim()
    return trimmed || message
  }, [message])

  const { recheckHardware, loading } = useRuntimeControl()

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        title="Hardware warning — click for details"
        className="flex items-center justify-center w-7 h-7 rounded-full
          bg-dam-red/15 border border-dam-red/40 text-dam-red
          hover:bg-dam-red/25 transition-colors"
      >
        <AlertTriangle size={13} strokeWidth={2.5} />
      </button>

      {open && (
        <>
          {/* backdrop to close */}
          <button type="button" aria-label="Close" className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-9 z-50 w-80 panel border border-dam-red/30 p-4 space-y-4 shadow-xl">
            <div className="flex items-center justify-between">
              <p className="text-dam-red text-xs font-bold uppercase tracking-wider">
                Hardware Not Connected
              </p>
              <button
                disabled={loading}
                onClick={() => { recheckHardware() }}
                className="flex items-center gap-1.5 px-2 py-1 rounded bg-dam-red/10 border border-dam-red/30
                  text-dam-red text-[10px] font-bold hover:bg-dam-red/20 disabled:opacity-50 transition-all uppercase"
              >
                {loading ? <Loader size={10} className="animate-spin" /> : <TrendingDown size={10} className="rotate-180" />}
                {loading ? 'Reloading...' : 'Reload Stackfile'}
              </button>
            </div>

            <pre className="text-[11px] text-dam-muted leading-snug font-mono whitespace-pre-wrap max-h-64 overflow-y-auto bg-dam-surface-1 rounded p-2 border border-dam-border/40">
              {cleaned}
            </pre>
            <div className="border-t border-dam-border/40 pt-2 text-[10px] text-dam-muted space-y-0.5">
              <p>Connect the device, then click <b>Reload Stackfile</b> above.</p>
              <p>Or go to{' '}
                <a href="/config" className="text-dam-blue hover:underline">Config</a>
                {' '}→ Apply &amp; Restart (full backend reboot).
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * Deadline margin indicator shown next to the Cycle Latency panel header.
 */
function SlackIndicator({ slackMs, deadlineMs }: { slackMs: number; deadlineMs: number }) {
  const pct    = deadlineMs > 0 ? slackMs / deadlineMs : 0
  const used   = deadlineMs - slackMs

  const { color, bg, label } =
    pct > 0.3  ? { color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/30', label: 'OK' }
    : pct > 0.1 ? { color: 'text-amber-400',   bg: 'bg-amber-400/10   border-amber-400/30',   label: 'NEAR' }
                : { color: 'text-dam-red',       bg: 'bg-dam-red/10     border-dam-red/30',     label: slackMs < 0 ? 'OVER' : 'TIGHT' }

  return (
    <div
      className={`flex items-center gap-1.5 rounded-md px-2 py-1 border text-[10px] font-bold ${bg} ${color}`}
      title={`Used ${used.toFixed(1)} ms of ${deadlineMs.toFixed(0)} ms budget — ${slackMs.toFixed(1)} ms slack`}
    >
      <Timer size={10} strokeWidth={2.5} />
      <span className="font-mono">{slackMs.toFixed(1)} ms</span>
      <span className="opacity-60">{label}</span>
    </div>
  )
}

export default function DashboardPage() {
  const tele = useTelemetry()
  const ctrl = useRuntimeControl()
  const demo = useDemoMode()
  const router = useRouter()
  const { liveMode, setLiveMode } = useLiveMode()

  // Auto-start cycles after demo launch brings the backend online
  useEffect(() => {
    if (!demo.readyToStart) return
    demo.clearReady()
    if (ctrl.status.state === 'idle' || ctrl.status.state === 'stopped') ctrl.start()
  }, [demo.readyToStart, demo.clearReady, ctrl.status.state, ctrl.start])

  // Three-way toggle for the right-column panel: Cycle Latency / Hardware
  // both render runtime metrics; "Go Live" switches the same panel to the
  // camera feed.  The tab is the single source of truth — it drives the
  // shared ``liveMode`` flag so the underlying WS subscription stays in
  // sync between this dashboard and the MCAP viewer.
  type MetricTab = 'latency' | 'hardware' | 'live'
  const [metricTab, setMetricTab] = useState<MetricTab>(liveMode ? 'live' : 'latency')
  useEffect(() => {
    setLiveMode(metricTab === 'live')
  }, [metricTab, setLiveMode])

  // Running-time display
  const [liveSegSec, setLiveSegSec] = useState(0)
  useEffect(() => {
    if (!ctrl.startedAt) { setLiveSegSec(0); return }
    const tick = () => setLiveSegSec(Math.floor((Date.now() - ctrl.startedAt!) / 1000))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [ctrl.startedAt])
  const totalRunSec = ctrl.accumulatedSec + (ctrl.status.state === 'running' ? liveSegSec : 0)

  const guards = Object.values(tele.guardMap)
  const risk   = tele.lastCycle?.risk_level ?? 'NORMAL'

  // 1-minute window stats
  const windowRejectPct = tele.windowCycles > 0 ? ((tele.windowRejects / tele.windowCycles) * 100).toFixed(1) + '%' : '0%'
  const windowClampPct  = tele.windowCycles > 0 ? ((tele.windowClamps  / tele.windowCycles) * 100).toFixed(1) + '%' : '0%'

  // Real-time Context
  const teleCycle = tele.lastCycle
  const ctrlStatus = ctrl.status

  const activeTask = teleCycle?.active_task ?? ctrlStatus.active_task ?? ctrlStatus.planned_task

  // Determine active boundaries without nested ternaries
  let activeBoundaries = ctrlStatus.planned_boundaries ?? []
  if (teleCycle?.active_boundaries?.length) {
    activeBoundaries = teleCycle.active_boundaries
  } else if (ctrlStatus.active_boundaries?.length) {
    activeBoundaries = ctrlStatus.active_boundaries
  }

  const isTaskLive = !!(teleCycle?.active_task || ctrlStatus.active_task)
  const controlFreqHz = ctrl.status.control_frequency_hz

  const startupError = ctrl.status.startup_error ?? null

  return (
    <PageShell
      title="Dashboard"
      subtitle="Real-time safety monitor & runtime control"
    >
      {/* Top bar */}
      {(tele.connected && (startupError || ctrl.status.error)) || (!tele.connected && demo.starting) ? (
      <div className="flex items-center justify-end gap-3 mb-4 -mt-2 min-h-[28px]">
        {tele.connected && (startupError || ctrl.status.error) && (
          <HardwareWarning message={startupError || ctrl.status.error || ""} />
        )}
        {!tele.connected && demo.starting && (
          <div className="flex items-center gap-1.5 text-[10px] bg-dam-surface-2/50 px-2 py-1 rounded-md border border-dam-border/50 max-w-[300px]">
              <span className="flex items-center gap-1 text-dam-muted whitespace-nowrap">
                <Loader size={10} className="animate-spin" /> Starting…
              </span>
          </div>
        )}
      </div>
      ) : null}

      {/* Main grid */}
      <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-5">

        {/* Left column */}
        <div className="space-y-4">
          <RiskGauge level={risk} />

          <div className="grid grid-cols-2 gap-2.5">
            <StatsCard
              label="Run Time"
              value={formatUptime(totalRunSec)}
              sub={undefined}
              icon={<Timer size={18} />}
            />
            <StatsCard
              label="Faults"
              value={tele.totalFaults}
              accent={tele.totalFaults > 0}
              icon={<AlertTriangle size={18} className="text-dam-red" />}
            />

            <StatsCard
              label="Rejects"
              value={tele.totalRejects}
              sub={`${windowRejectPct} per 1 min`}
              accent={tele.totalRejects > 0}
              icon={<Shield size={18} />}
            />
            <StatsCard
              label="Clamps"
              value={tele.totalClamps}
              sub={`${windowClampPct} per 1 min`}
              accent={tele.totalClamps > 0}
              icon={<TrendingDown size={18} />}
            />
          </div>

          {/* Runtime Context Card */}
          <div className="panel p-4 space-y-3">
            <div className="flex items-center justify-between">
              <p className="section-label">Runtime Context</p>
            </div>

            <div className="space-y-2">
              <div className="flex justify-between items-center bg-dam-surface-2 rounded-lg px-3 py-2 border border-dam-border/40">
                <span className="text-[11px] text-dam-muted">Active Task</span>
                <span className={`text-[11px] font-mono font-bold ${isTaskLive ? 'text-dam-blue' : 'text-dam-muted'}`}>
                  {activeTask || '—'}
                  {!isTaskLive && activeTask && <span className="ml-1 text-[9px] opacity-60">(standby)</span>}
                </span>
              </div>
              <div className="flex justify-between items-center bg-dam-surface-2 rounded-lg px-3 py-2 border border-dam-border/40">
                <span className="text-[11px] text-dam-muted">Active Boundaries</span>
                <span className={`text-[11px] font-mono font-bold ${isTaskLive ? 'text-dam-blue' : 'text-dam-muted'}`}>
                  {activeBoundaries.length} {isTaskLive ? 'Active' : 'Configured'}
                </span>
              </div>
              <div className="flex justify-between items-center bg-dam-surface-2 rounded-lg px-3 py-2 border border-dam-border/40">
                <span className="text-[11px] text-dam-muted">Control Freq</span>
                <span className="text-[11px] font-mono font-bold text-dam-text">
                  {controlFreqHz ? `${controlFreqHz.toFixed(1)} Hz` : '—'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Right column */}
        <div className="space-y-4 min-w-0">
          <div className="panel p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex gap-1 bg-dam-surface-2 border border-dam-border rounded-lg p-0.5">
                {(['latency', 'hardware', 'live'] as const).map((name) => {
                  const label =
                    name === 'latency' ? 'Cycle Latency' :
                    name === 'hardware' ? 'Hardware' : 'Go Live'
                  const active = metricTab === name
                  // "live" keeps a red accent when active so the active-stream
                  // state is glanceable; the rest stay blue.  No icons — all
                  // three tabs share the same text-only shape.
                  const activeStyle = name === 'live'
                    ? 'bg-red-500/15 text-red-400 border border-red-500/30'
                    : 'bg-dam-blue/15 text-dam-blue border border-dam-blue/30'
                  return (
                    <button
                      key={name}
                      onClick={() => setMetricTab(name)}
                      className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-colors ${
                        active ? activeStyle : 'text-dam-muted border border-transparent hover:text-dam-text'
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
              <div className="flex items-center gap-2">
                {metricTab === 'latency' && tele.latestPerf != null && (
                  <SlackIndicator
                    slackMs={tele.latestPerf.slack_ms}
                    deadlineMs={tele.latestPerf.deadline_ms}
                  />
                )}
              </div>
            </div>

            {metricTab === 'live' ? (
              <div className="h-80">
                <McapCameraPlayer
                  filename=""
                  cameras={tele.activeCameras}
                  currentTimestampNs={null}
                  liveMode
                />
              </div>
            ) : metricTab === 'latency' ? (
              <LatencyChart
                data={tele.latencyHistory}
                perf={tele.latestPerf}
                cycleIds={tele.latencyCycleIds}
                onCycleClick={(cycleId) => router.push(`/risk-log?cycle_id=${cycleId}`)}
              />
            ) : (
              <HardwarePanel
                hardware={tele.lastCycle?.hardware}
                taskLive={ctrl.status.state === 'running'}
              />
            )}
          </div>

          <div className="panel p-4">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Shield size={16} className="text-dam-muted" />
                <p className="section-label">Guard Status</p>
              </div>
            </div>

            <GuardTable
              guards={guards}
              activeTask={activeTask}
              activeBoundaries={activeBoundaries}
              allBoundaryConfigs={ctrl.boundaries}
              latestCycleId={tele.lastCycle?.cycle_id}
              onGuardClick={(cycleId) => router.push(`/risk-log?cycle_id=${cycleId}`)}
            />

            {/* Status indicators (Persistent L0-L3) */}
            <div className="mt-4 pt-3 border-t border-dam-border/40 flex gap-4 overflow-x-auto pb-1">
              {['L0', 'L1', 'L2', 'L3'].map(layer => {
                const layerGuards = guards.filter(g => g.layer === layer);
                const hasGuards = layerGuards.length > 0;

                let worst: string = 'OFF';
                let colorCls = 'bg-dam-muted/70 text-dam-muted';
                let shadowCls = '';
                let pulseCls = '';

                if (hasGuards) {
                  // Determine worst decision by priority: FAULT > REJECT > CLAMP > PASS
                  if (layerGuards.some(g => g.decision === 'FAULT')) {
                    worst = 'FAULT'
                  } else if (layerGuards.some(g => g.decision === 'REJECT')) {
                    worst = 'REJECT'
                  } else if (layerGuards.some(g => g.decision === 'CLAMP')) {
                    worst = 'CLAMP'
                  } else {
                    worst = 'PASS'
                  }

                  const cfg = DEC_CONFIG[worst as keyof typeof DEC_CONFIG];
                  colorCls = cfg.color.replaceAll('text-', 'bg-');
                  shadowCls = 'shadow-[0_0_8px] shadow-current';
                  pulseCls = 'animate-pulse';
                }

                return (
                  <div key={layer} className={`flex items-center gap-1.5 shrink-0 transition-opacity ${hasGuards ? 'opacity-100' : 'opacity-75'}`}>
                    <div className={`w-2 h-2 rounded-full ${colorCls} ${shadowCls} ${pulseCls}`} />
                    <span className="text-[10px] font-bold uppercase tracking-tighter text-dam-muted">{layer}</span>
                    {!hasGuards && <span className="text-[8px] font-black opacity-50 -ml-0.5 tracking-tighter">OFF</span>}
                  </div>
                );
              })}
            </div>
          </div>

        </div>
      </div>
    </PageShell>
  )
}
