'use client'
import React, { useEffect, useState, Suspense, useCallback } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import type { McapSessionSummary, McapSessionDetail, McapCycle, McapCycleDetail } from '@/lib/api'
import { McapTimelineView } from '@/components/McapTimelineView'
import { McapCycleInspector } from '@/components/McapCycleInspector'
import { McapCameraPlayer } from '@/components/McapCameraPlayer'
import { PageShell } from '@/components/PageShell'
import { useTelemetry } from '@/hooks/useTelemetry'
import { useLiveMode } from '@/hooks/useLiveMode'
import {
  Film, Download, Loader2, AlertCircle, FileText,
  Activity, AlertTriangle, ShieldAlert, Clock, Radio, Trash2,
} from 'lucide-react'

// Helper: convert WS CycleEvent to McapCycleDetail shape for the inspector
function cycleEventToDetail(cycle: NonNullable<ReturnType<typeof useTelemetry>['lastCycle']>): McapCycleDetail {
  return {
    cycle_id: cycle.cycle_id,
    timestamp_ns: cycle.timestamp * 1e9,
    timestamp: cycle.timestamp,
    has_violation: cycle.was_rejected,
    has_clamp: cycle.was_clamped,
    violated_layer_mask: 0,
    clamped_layer_mask: 0,
    violated_layers: cycle.guard_statuses.filter(g => g.decision === 'FAULT' || g.decision === 'REJECT').map(g => g.layer),
    clamped_layers: cycle.guard_statuses.filter(g => g.decision === 'CLAMP').map(g => g.layer),
    active_task: cycle.active_task ?? null,
    active_boundaries: cycle.active_boundaries ?? [],
    source_ms: cycle.latency_ms?.source ?? 0,
    policy_ms: cycle.latency_ms?.policy ?? 0,
    guards_ms: cycle.latency_ms?.guards ?? 0,
    sink_ms: cycle.latency_ms?.sink ?? 0,
    total_ms: cycle.latency_ms?.total ?? 0,
    latency: cycle.latency_ms ?? {},
    observation: null,
    action: null,
    guard_results: cycle.guard_statuses.map(g => ({
      guard_name: g.name,
      layer: parseInt(g.layer.replace('L', ''), 10),
      layer_name: g.layer,
      decision: 0,
      decision_name: g.decision,
      reason: '',
      latency_ms: null,
      is_violation: g.decision === 'FAULT' || g.decision === 'REJECT',
      is_clamp: g.decision === 'CLAMP',
      fault_source: null,
    })),
  }
}

// ── Session list card ─────────────────────────────────────────────────────────

function SessionCard({
  session, detail, selected, onClick,
}: {
  session: McapSessionSummary
  detail: McapSessionDetail | null
  selected: boolean
  onClick: () => void
}) {
  const violations = detail?.stats.violation_cycles ?? 0
  const clamps = detail?.stats.clamp_cycles ?? 0

  return (
    <button
      onClick={onClick}
      className={[
        'w-full text-left p-3 rounded-lg border transition-all duration-150 space-y-2',
        selected
          ? 'bg-dam-blue/10 border-dam-blue/40 shadow-sm'
          : 'bg-dam-surface-2 border-dam-border/60 hover:border-dam-blue/30 hover:bg-dam-surface-1',
      ].join(' ')}
    >
      <div className="flex items-start gap-2">
        <Film size={13} className={`shrink-0 mt-0.5 ${selected ? 'text-dam-blue' : 'text-dam-muted'}`} />
        <div className="flex-1 min-w-0">
          <p className="font-mono text-[11px] font-semibold text-dam-text truncate">{session.filename}</p>
          <p className="text-[10px] text-dam-muted mt-0.5">
            {new Date(session.created_at * 1000).toLocaleString([], {
              month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
            })}
          </p>
        </div>
        <span className="text-[10px] font-mono text-dam-muted shrink-0">{session.size_mb.toFixed(1)} MB</span>
      </div>

      {detail && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="flex items-center gap-1 text-[10px] text-dam-muted bg-dam-surface-1 px-1.5 py-0.5 rounded border border-dam-border">
            <Activity size={9} />
            {detail.stats.total_cycles.toLocaleString()}
          </span>
          {violations > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded border border-red-500/20">
              <AlertTriangle size={9} />
              {violations}
            </span>
          )}
          {clamps > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-dam-blue bg-blue-500/10 px-1.5 py-0.5 rounded border border-blue-500/20">
              <ShieldAlert size={9} />
              {clamps}
            </span>
          )}
          {detail.stats.duration_sec > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-dam-muted ml-auto">
              <Clock size={9} />
              {detail.stats.duration_sec.toFixed(1)}s
            </span>
          )}
        </div>
      )}
    </button>
  )
}

// ── Session header bar ────────────────────────────────────────────────────────

function SessionHeader({ session, detail }: { session: McapSessionSummary; detail: McapSessionDetail }) {
  const [metaOpen, setMetaOpen] = useState(false)
  const meta = detail.metadata ?? {}
  const hasMeta = Object.keys(meta).length > 0

  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-4 py-3 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Film size={14} className="text-dam-blue shrink-0" />
          <span className="font-mono text-sm font-bold text-dam-text truncate">{session.filename}</span>
          <span className="text-xs text-dam-muted shrink-0">{session.size_mb.toFixed(1)} MB</span>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-dam-muted bg-dam-surface-1 px-2 py-1 rounded border border-dam-border">
            {detail.stats.total_cycles.toLocaleString()} cycles
          </span>
          {detail.stats.violation_cycles > 0 && (
            <span className="text-xs text-red-400 bg-red-500/10 px-2 py-1 rounded border border-red-500/20">
              {detail.stats.violation_cycles} violations
            </span>
          )}
          {detail.stats.clamp_cycles > 0 && (
            <span className="text-xs text-dam-blue bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">
              {detail.stats.clamp_cycles} clamps
            </span>
          )}
          {detail.stats.duration_sec > 0 && (
            <span className="text-xs text-dam-muted">{detail.stats.duration_sec.toFixed(1)} s</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {hasMeta && (
            <button
              onClick={() => setMetaOpen(v => !v)}
              className="text-[10px] text-dam-muted hover:text-dam-text border border-dam-border px-2 py-1 rounded hover:border-dam-blue/30 transition-colors"
            >
              {metaOpen ? 'Hide' : 'Metadata'}
            </button>
          )}
          <a
            href={api.mcapDownloadUrl(session.filename)}
            className="flex items-center gap-1.5 text-[10px] font-bold text-dam-blue bg-dam-blue/10 hover:bg-dam-blue/20 border border-dam-blue/30 px-2.5 py-1 rounded transition-colors"
          >
            <Download size={11} />
            Download
          </a>
          <button
            onClick={() => {
              if (confirm('Are you sure you want to delete this session?')) {
                api.deleteMcapSession(session.filename).then(() => { window.location.href = '/mcap-viewer' })
              }
            }}
            className="flex items-center justify-center p-1 text-dam-muted hover:text-red-400 hover:bg-red-500/10 border border-transparent hover:border-red-500/50 rounded transition-colors"
            title="Delete Session"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {metaOpen && hasMeta && (
        <div className="pt-2 border-t border-dam-border/40 grid grid-cols-2 sm:grid-cols-3 gap-1">
          {Object.entries(meta).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5 text-[10px]">
              <span className="text-dam-muted shrink-0">{k}:</span>
              <span className="font-mono text-dam-text/80 truncate" title={v}>{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Live Mode Panel ───────────────────────────────────────────────────────────

function LiveModePanel() {
  const { lastCycle, connected } = useTelemetry()
  const liveCameras = lastCycle?.live_images ? Object.keys(lastCycle.live_images) : []

  // Convert latest WS cycle into a fake McapCycleDetail for the inspector
  const liveDetail: McapCycleDetail | null = lastCycle ? cycleEventToDetail(lastCycle) : null

  // Build a live cycle timeline feed (append-only)
  const [liveCycles, setLiveCycles] = useState<McapCycle[]>([])
  useEffect(() => {
    if (!lastCycle) return
    setLiveCycles(prev => {
      if (prev.length > 0 && prev[prev.length - 1].cycle_id >= lastCycle.cycle_id) return prev
      const entry: McapCycle = {
        cycle_id: lastCycle.cycle_id,
        seq: lastCycle.cycle_id,
        timestamp_ns: lastCycle.timestamp * 1e9,
        timestamp: lastCycle.timestamp,
        has_violation: lastCycle.was_rejected,
        has_clamp: lastCycle.was_clamped,
        violated_layer_mask: 0,
        clamped_layer_mask: 0,
        violated_layers: lastCycle.guard_statuses.filter(g => g.decision === 'FAULT' || g.decision === 'REJECT').map(g => g.layer),
        clamped_layers: lastCycle.guard_statuses.filter(g => g.decision === 'CLAMP').map(g => g.layer),
      }
      const next = [...prev.slice(-499), entry] // keep last 500
      return next
    })
  }, [lastCycle])

  const [selectedLiveCycleId, setSelectedLiveCycleId] = useState<number | null>(null)
  // Auto-follow latest cycle unless user has selected a specific one
  const autoFollow = selectedLiveCycleId === null
  const displayCycleId = autoFollow ? (lastCycle?.cycle_id ?? null) : selectedLiveCycleId

  if (!connected) {
    return (
      <div className="flex-1 flex items-center justify-center text-dam-muted">
        <div className="text-center space-y-2">
          <Radio size={32} className="mx-auto opacity-30 animate-pulse" />
          <p className="text-sm">Connecting to telemetry…</p>
        </div>
      </div>
    )
  }

  if (!lastCycle) {
    return (
      <div className="flex-1 flex items-center justify-center text-dam-muted">
        <div className="text-center space-y-2">
          <Radio size={32} className="mx-auto opacity-30" />
          <p className="text-sm">Waiting for control loop data…</p>
          <p className="text-xs opacity-60">Start a loop on the Dashboard to begin</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col gap-4">
      {/* Live banner */}
      <div className="bg-red-500/5 border border-red-500/20 rounded-lg px-4 py-3 flex items-center gap-3">
        <Radio size={14} className="text-red-400 animate-pulse shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-red-400">Live Mode</p>
          <p className="text-[10px] text-dam-muted mt-0.5">
            Showing real-time data from WebSocket telemetry • Cycle {lastCycle.cycle_id}
          </p>
        </div>
        <span className="text-[10px] font-mono text-red-400/60 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20">
          {lastCycle.risk_level}
        </span>
      </div>

      {/* Timeline */}
      <div className="bg-dam-surface-2 border border-dam-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em]">Live Cycle Timeline</p>
          {!autoFollow && (
            <button
              onClick={() => setSelectedLiveCycleId(null)}
              className="text-[10px] text-dam-blue hover:underline"
            >
              ↓ Follow latest
            </button>
          )}
        </div>
        <McapTimelineView
          cycles={liveCycles}
          selectedCycleId={displayCycleId ?? undefined}
          onSelectCycle={id => setSelectedLiveCycleId(id)}
        />
      </div>

      {/* Inspector + camera */}
      <div className="flex gap-4 flex-1 min-h-0" style={{ minHeight: 360 }}>
        {/* Inspector — uses WS data, not MCAP API */}
        <div className="flex-1 min-w-0">
          <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
            Live Inspector {!autoFollow ? `— Cycle ${displayCycleId}` : '— Latest'}
          </p>
          <div className="h-[360px]">
            {liveDetail ? (
              <McapCycleInspector
                filename=""
                cycleId={displayCycleId}
                overrideCycleDetail={autoFollow ? liveDetail : undefined}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-dam-muted text-sm">
                Waiting for cycle data…
              </div>
            )}
          </div>
        </div>

        {/* Camera — shows WS live feed */}
        <div className={`${liveCameras.length > 1 ? 'w-[560px]' : 'w-80'} shrink-0`}>
          <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">Live Camera</p>
          <div className="h-[360px]">
            <McapCameraPlayer
              filename=""
              cameras={liveCameras}
              currentTimestampNs={null}
              liveImages={lastCycle?.live_images ?? null}
              liveMode
            />
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main content (needs Suspense for useSearchParams) ─────────────────────────

function McapViewerContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { liveMode, toggleLiveMode } = useLiveMode()

  // URL-driven state
  const [selectedFilename, setSelectedFilename] = useState<string | null>(
    searchParams.get('filename')
  )
  const [selectedCycleId, setSelectedCycleId] = useState<number | null>(
    searchParams.get('cycle_id') ? Number(searchParams.get('cycle_id')) : null
  )

  // Loaded session data
  const [sessions, setSessions] = useState<McapSessionSummary[]>([])
  const [detailMap, setDetailMap] = useState<Record<string, McapSessionDetail>>({})
  const [cycles, setCycles] = useState<McapCycle[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [cyclesLoading, setCyclesLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectSession = useCallback((filename: string) => {
    setSelectedFilename(filename)
    setSelectedCycleId(null)
    setCycles([])
    const url = new URL(window.location.href)
    url.searchParams.set('filename', filename)
    url.searchParams.delete('cycle_id')
    router.replace(url.pathname + url.search, { scroll: false })
  }, [router])

  const selectCycle = useCallback((cycleId: number) => {
    setSelectedCycleId(cycleId)
    const url = new URL(window.location.href)
    url.searchParams.set('cycle_id', String(cycleId))
    router.replace(url.pathname + url.search, { scroll: false })
  }, [router])

  // Load sessions — only when NOT in live mode
  const loadSessions = useCallback(async (opts?: { showSpinner?: boolean }) => {
    if (liveMode) return
    if (opts?.showSpinner) setSessionsLoading(true)
    try {
      const data = await api.listMcapSessions()
      const list = data?.sessions ?? []
      setSessions(list)

      setSelectedFilename(prev => {
        if (prev) return prev
        const urlFile = searchParams.get('filename')
        return list.length > 0 ? (urlFile ?? list[0].filename) : null
      })

      setDetailMap(prev => {
        const toFetch = list.filter(s => !prev[s.filename])
        if (toFetch.length === 0) return prev
        Promise.allSettled(toFetch.map(s => api.getMcapSession(s.filename)))
          .then(results => {
            setDetailMap(curr => {
              const next = { ...curr }
              results.forEach((r, i) => {
                if (r.status === 'fulfilled' && r.value?.stats) {
                  next[toFetch[i].filename] = r.value
                }
              })
              return next
            })
          })
          .catch(() => {})
        return prev
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load sessions')
    } finally {
      if (opts?.showSpinner) setSessionsLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, liveMode])

  // Trigger session load when live mode is turned OFF
  useEffect(() => {
    if (!liveMode) {
      void loadSessions({ showSpinner: true })
    }
  }, [liveMode, loadSessions])

  // Refresh on focus / system updates (MCAP mode only)
  useEffect(() => {
    if (liveMode) return
    const onFocus = () => void loadSessions()
    const onUpdate = () => void loadSessions()
    document.addEventListener('visibilitychange', onFocus)
    window.addEventListener('dam-system-update', onUpdate)
    return () => {
      document.removeEventListener('visibilitychange', onFocus)
      window.removeEventListener('dam-system-update', onUpdate)
    }
  }, [loadSessions, liveMode])

  // Navigate via cycle_id in URL (only in MCAP mode)
  useEffect(() => {
    if (liveMode) return
    const urlCycleId = searchParams.get('cycle_id')
    const urlFile = searchParams.get('filename')
    if (!urlCycleId || urlFile) return

    const id = Number(urlCycleId)
    api.findMcapSession(id)
      .then(res => {
        if (res.found && res.filename) {
          selectSession(res.filename)
          setSelectedCycleId(id)
        }
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Load cycles when selected session changes (MCAP mode)
  useEffect(() => {
    if (liveMode || !selectedFilename) return
    let cancelled = false
    setCyclesLoading(true)
    setCycles([])
    api.listMcapCycles(selectedFilename)
      .then(data => {
        if (cancelled) return
        const list = data?.cycles ?? []
        setCycles(list)
        const urlCycleId = searchParams.get('cycle_id')
        if (urlCycleId) {
          const id = Number(urlCycleId)
          if (list.some(c => c.cycle_id === id)) setSelectedCycleId(id)
        }
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load cycles') })
      .finally(() => { if (!cancelled) setCyclesLoading(false) })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFilename, liveMode])

  const selectedDetail = selectedFilename ? detailMap[selectedFilename] ?? null : null
  const cameras = selectedDetail?.stats.cameras ?? []
  const selectedCycle = cycles.find(c => c.cycle_id === selectedCycleId)

  return (
    <div className="flex flex-col min-h-[calc(100vh-120px)] gap-5">
      {/* Live mode toggle — top right, consistent with Dashboard */}
      <div className="flex items-center justify-end gap-3 -mt-2 min-h-[28px]">
        <button
          onClick={toggleLiveMode}
          title={liveMode ? 'Switch to MCAP file player' : 'Switch to live WebSocket feed'}
          className={`flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1 rounded-md border transition-all ${
            liveMode
              ? 'bg-red-500/15 border-red-500/40 text-red-400'
              : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-blue/30 hover:text-dam-blue'
          }`}
        >
          <Radio size={10} className={liveMode ? 'animate-pulse' : ''} />
          {liveMode ? 'Live Mode' : 'Go Live'}
        </button>
      </div>

      {/* ── LIVE MODE ───────────────────────────────────────────────────────── */}
      {liveMode ? (
        <div className="flex gap-5 flex-1">
          <LiveModePanel />
        </div>
      ) : (
        /* ── MCAP FILE MODE ──────────────────────────────────────────────── */
        <div className="flex gap-5 flex-1">
          {/* Left: session list */}
          <div className="w-64 shrink-0 flex flex-col gap-2">
            <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] px-1">
              Sessions ({sessions.length})
            </p>

            {sessionsLoading ? (
              <div className="flex items-center gap-2 text-dam-muted py-8 justify-center">
                <Loader2 size={16} className="animate-spin" />
                <span className="text-sm">Loading…</span>
              </div>
            ) : sessions.length === 0 ? (
              <div className="py-8 text-center text-dam-muted space-y-2">
                <FileText size={28} className="mx-auto opacity-30" />
                <p className="text-sm">No sessions recorded yet</p>
                <p className="text-[10px] opacity-60">Enable Live Mode to see real-time data</p>
              </div>
            ) : (
              <div className="space-y-1.5 overflow-y-auto">
                {sessions.map(s => (
                  <SessionCard
                    key={s.filename}
                    session={s}
                    detail={detailMap[s.filename] ?? null}
                    selected={selectedFilename === s.filename}
                    onClick={() => selectSession(s.filename)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Right: session detail */}
          <div className="flex-1 min-w-0 flex flex-col gap-4">
            {!selectedFilename ? (
              <div className="flex-1 flex items-center justify-center text-dam-muted">
                <div className="text-center space-y-2">
                  <Film size={32} className="mx-auto opacity-30" />
                  <p className="text-sm">Select a session to view details</p>
                  <p className="text-[10px] opacity-60">Or enable Live Mode to watch in real-time</p>
                </div>
              </div>
            ) : (
              <>
                {selectedDetail ? (
                  <SessionHeader
                    session={sessions.find(s => s.filename === selectedFilename)!}
                    detail={selectedDetail}
                  />
                ) : (
                  <div className="bg-dam-surface-2 border border-dam-border rounded-lg p-4 flex items-center gap-2 text-dam-muted text-sm">
                    <Loader2 size={14} className="animate-spin" />
                    Loading session info…
                  </div>
                )}

                {/* Timeline */}
                <div className="bg-dam-surface-2 border border-dam-border rounded-lg p-4">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-3">
                    Cycle Timeline
                  </p>
                  {cyclesLoading ? (
                    <div className="flex items-center gap-2 text-dam-muted py-4 justify-center">
                      <Loader2 size={14} className="animate-spin" />
                      <span className="text-sm">Indexing cycles…</span>
                    </div>
                  ) : (
                    <McapTimelineView
                      cycles={cycles}
                      selectedCycleId={selectedCycleId ?? undefined}
                      onSelectCycle={selectCycle}
                    />
                  )}
                </div>

                {/* Inspector + camera */}
                <div className="flex gap-4 flex-1 min-h-0" style={{ minHeight: 360 }}>
                  <div className="flex-1 min-w-0">
                    <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                      Cycle Inspector
                    </p>
                    <div className="h-[360px]">
                      <McapCycleInspector
                        filename={selectedFilename}
                        cycleId={selectedCycleId}
                      />
                    </div>
                  </div>

                  <div className={`${cameras.length > 1 ? 'w-[560px]' : 'w-80'} shrink-0`}>
                    <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                      Camera Footage
                    </p>
                    <div className="h-[360px]">
                      <McapCameraPlayer
                        filename={selectedFilename}
                        cameras={cameras}
                        currentTimestampNs={selectedCycle?.timestamp_ns ?? null}
                      />
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Error toast */}
      {error && (
        <div className="fixed bottom-4 right-4 max-w-sm p-3 bg-red-500/10 border border-red-500/30 rounded-lg flex items-start gap-2 shadow-lg z-50">
          <AlertCircle size={14} className="text-red-400 shrink-0 mt-0.5" />
          <p className="text-xs text-red-400">{error}</p>
          <button onClick={() => setError(null)} className="ml-auto text-red-400/60 hover:text-red-400 text-xs">✕</button>
        </div>
      )}
    </div>
  )
}

export default function McapViewerPage() {
  return (
    <PageShell title="MCAP Sessions" subtitle="Review control cycles, incidents & camera footage">
      <Suspense fallback={
        <div className="flex items-center justify-center h-64 text-dam-muted gap-2">
          <Loader2 size={20} className="animate-spin" />
          <span>Loading…</span>
        </div>
      }>
        <McapViewerContent />
      </Suspense>
    </PageShell>
  )
}
