'use client'
import { useState, useEffect }    from 'react'
import { useRouter }         from 'next/navigation'
import { useTelemetry }      from '@/hooks/useTelemetry'
import { useRuntimeControl } from '@/hooks/useRuntimeControl'
import { useDemoMode }       from '@/hooks/useDemoMode'
import { useLiveMode }       from '@/hooks/useLiveMode'
import { RiskGauge }         from '@/components/RiskGauge'
import { StatsCard }         from '@/components/StatsCard'
import { GuardTable }        from '@/components/GuardTable'
import { LatencyChart }      from '@/components/LatencyChart'
import { McapCameraPlayer }  from '@/components/McapCameraPlayer'
import { Shield, TrendingDown, Timer, Loader, AlertTriangle, Radio } from 'lucide-react'
import { PageShell } from '@/components/PageShell'
import { DEC_CONFIG } from '@/components/GuardTable'
import { api } from '@/lib/api'

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
        setLabel(adapter === 'lerobot' ? 'LeRobot server' : adapter === 'ros2' ? 'ROS2 server' : 'Dev server')
      }
    } catch { /* ignore */ }
  }, [])
  return label
}

/** Warning icon + popover listing missing hardware devices. */
function HardwareWarning({ message }: { message: string }) {
  const [open, setOpen] = useState(false)

  // Parse bullet-point lines out of the error message for clean display
  const lines = message
    .split('\n')
    .map(l => l.replace(/^\s*[•\-]\s*/, '').trim())
    .filter(Boolean)

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
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
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
                {loading ? 'Rechecking...' : 'Recheck'}
              </button>
            </div>

            <ul className="space-y-1">
              {lines.map((l, i) => (
                <li key={i} className="text-[11px] text-dam-muted leading-snug flex gap-1.5">
                  <span className="text-dam-red shrink-0 mt-0.5">•</span>
                  <span>{l}</span>
                </li>
              ))}
            </ul>
            <div className="border-t border-dam-border/40 pt-2 text-[10px] text-dam-muted space-y-0.5">
              <p>Connect the device, then click <b>Recheck</b> above.</p>
              <p>Or go to{' '}
                <a href="/config" className="text-dam-blue hover:underline">Config</a>
                {' '}→ Apply &amp; Restart.
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
  const adapterLabel = useAdapterLabel()
  const router = useRouter()
  const { liveMode, toggleLiveMode } = useLiveMode()

  // Camera footage toggle state (only for MCAP session fetching when NOT in live mode)
  const [showCamera, setShowCamera] = useState(false)
  const [mcapSession, setMcapSession] = useState<string | null>(null)
  const [mcapCameras, setMcapCameras] = useState<string[]>([])
  const [cameraLoading, setCameraLoading] = useState(false)

  // Derive cameras for live mode from WS data
  const liveCameras = tele.lastCycle?.live_images ? Object.keys(tele.lastCycle.live_images) : []

  // showCamera is true either when user clicked camera button OR live mode is on
  const actuallyShowCamera = showCamera || liveMode

  // Auto-start cycles after demo launch brings the backend online
  useEffect(() => {
    if (demo.readyToStart) {
      demo.clearReady()
      if (ctrl.status.state === 'idle' || ctrl.status.state === 'stopped') {
        ctrl.start()
      }
    }
  }, [demo, ctrl])

  // Load MCAP session only when camera is shown AND NOT in live mode
  useEffect(() => {
    if (!showCamera || liveMode) {
      setMcapSession(null)
      setMcapCameras([])
      return
    }

    setCameraLoading(true)
    api.listMcapSessions()
      .then(data => {
        const sessions = data?.sessions ?? []
        if (sessions.length > 0) {
          const filename = sessions[0].filename
          setMcapSession(filename)
          return api.getMcapSession(filename)
        }
      })
      .then(sessionDetail => {
        if (sessionDetail?.stats?.cameras) {
          setMcapCameras(sessionDetail.stats.cameras)
        }
      })
      .catch(() => {
        setMcapSession(null)
        setMcapCameras([])
      })
      .finally(() => setCameraLoading(false))
  }, [showCamera, liveMode])

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
  const activeTask = tele.lastCycle?.active_task
    ?? ctrl.status.active_task
    ?? ctrl.status.planned_task
  const activeBoundaries = (
    tele.lastCycle?.active_boundaries?.length
      ? tele.lastCycle.active_boundaries
      : ctrl.status.active_boundaries?.length
        ? ctrl.status.active_boundaries
        : ctrl.status.planned_boundaries ?? []
  )
  const isTaskLive = !!(tele.lastCycle?.active_task || ctrl.status.active_task)
  const controlFreqHz = ctrl.status.control_frequency_hz ?? 50.0

  const startupError = ctrl.status.startup_error ?? null

  return (
    <PageShell
      title="Dashboard"
      subtitle="Real-time safety monitor & runtime control"
    >
      {/* Top bar */}
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

        {/* Live mode toggle — same style as MCAP Viewer, lives in top-right */}
        <button
          onClick={toggleLiveMode}
          title={liveMode ? 'Turn off live camera mode' : 'Turn on live camera mode'}
          className={`flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1 rounded-md border transition-all ${
            liveMode
              ? 'bg-red-500/15 border-red-500/40 text-red-400'
              : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-blue/30 hover:text-dam-blue'
          }`}
        >
          <Radio size={10} className={liveMode ? 'animate-pulse' : ''} />
          {liveMode ? 'Live Camera' : 'Go Live'}
        </button>
      </div>

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
                <span className="text-[11px] font-mono font-bold text-dam-text">{controlFreqHz.toFixed(1)} Hz</span>
              </div>
            </div>
          </div>
        </div>

        {/* Right column */}
        <div className="space-y-4 min-w-0">
          <div className="panel p-4">
            <div className="flex items-center justify-between mb-3">
              <p className="section-label">
                {actuallyShowCamera
                  ? liveMode ? 'Live Camera Feed' : 'Camera Footage'
                  : 'Cycle Latency'}
              </p>
              <div className="flex items-center gap-2">
                {!actuallyShowCamera && tele.latestPerf != null && (
                  <SlackIndicator
                    slackMs={tele.latestPerf.slack_ms}
                    deadlineMs={tele.latestPerf.deadline_ms}
                  />
                )}
                {/* Camera button removed — use Live Camera toggle in top bar instead */}
              </div>
            </div>

            {actuallyShowCamera ? (
              <div className="h-80">
                {liveMode ? (
                  /* Live mode: show WS camera feed */
                  <McapCameraPlayer
                    filename=""
                    cameras={liveCameras}
                    currentTimestampNs={null}
                    liveImages={tele.lastCycle?.live_images ?? null}
                    liveMode
                  />
                ) : cameraLoading ? (
                  <div className="h-full flex items-center justify-center text-dam-muted gap-2">
                    <Loader size={16} className="animate-spin" />
                    <span className="text-sm">Loading session…</span>
                  </div>
                ) : mcapSession && mcapCameras.length > 0 ? (
                  <>
                    <McapCameraPlayer
                      filename={mcapSession}
                      cameras={mcapCameras}
                      currentTimestampNs={
                        tele.lastCycle?.timestamp
                          ? tele.lastCycle.timestamp * 1e9
                          : null
                      }
                    />
                    <p className="text-[10px] text-dam-muted/60 text-center mt-1">
                      Current frame • For playback controls, go to <a href="/mcap-viewer" className="text-dam-blue hover:underline">MCAP Viewer</a>
                    </p>
                  </>
                ) : mcapSession ? (
                  <div className="h-full flex items-center justify-center text-dam-muted">
                    <p className="text-sm">No camera footage captured yet</p>
                  </div>
                ) : (
                  <div className="h-full flex items-center justify-center text-dam-muted">
                    <p className="text-sm">No session found</p>
                  </div>
                )}
              </div>
            ) : (
              <LatencyChart
                data={tele.latencyHistory}
                perf={tele.latestPerf}
                cycleIds={tele.latencyCycleIds}
                onCycleClick={(cycleId) => router.push(`/risk-log?cycle_id=${cycleId}`)}
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

            {/* Status indicators (Persistent L0-L4) */}
            <div className="mt-4 pt-3 border-t border-dam-border/40 flex gap-4 overflow-x-auto pb-1">
              {['L0', 'L1', 'L2', 'L3', 'L4'].map(layer => {
                const layerGuards = guards.filter(g => g.layer === layer);
                const hasGuards = layerGuards.length > 0;

                let worst: string = 'OFF';
                let colorCls = 'bg-dam-muted/20 text-dam-muted/40';
                let shadowCls = '';
                let pulseCls = '';

                if (hasGuards) {
                  worst = layerGuards.some(g => g.decision === 'FAULT') ? 'FAULT' :
                          layerGuards.some(g => g.decision === 'REJECT') ? 'REJECT' :
                          layerGuards.some(g => g.decision === 'CLAMP') ? 'CLAMP' : 'PASS';
                  const cfg = DEC_CONFIG[worst as keyof typeof DEC_CONFIG];
                  colorCls = cfg.color.replace('text-', 'bg-');
                  shadowCls = 'shadow-[0_0_8px] shadow-current';
                  pulseCls = 'animate-pulse';
                }

                return (
                  <div key={layer} className={`flex items-center gap-1.5 shrink-0 transition-opacity ${hasGuards ? 'opacity-100' : 'opacity-40'}`}>
                    <div className={`w-2 h-2 rounded-full ${colorCls} ${shadowCls} ${pulseCls}`} />
                    <span className="text-[10px] font-bold uppercase tracking-tighter text-dam-muted">{layer}</span>
                    {!hasGuards && <span className="text-[8px] font-black opacity-30 -ml-0.5 tracking-tighter">OFF</span>}
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
