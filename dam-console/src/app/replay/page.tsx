'use client'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { PageShell } from '@/components/PageShell'
import { api } from '@/lib/api'
import type { McapSessionSummary } from '@/lib/api'
import { Play, Square, Loader2, AlertTriangle, Wrench, CheckCircle2 } from 'lucide-react'

const LIVE = '__live__'

interface ReplayGuard {
  name: string
  layer: number
  decision: string
  reason: string
}
interface Divergence {
  cycle: number
  recorded: string
  replayed: string
  guards: ReplayGuard[]
}
interface ChangeDriver {
  name: string
  layer: number
  count: number
  decisions: string[]
  sample_reason: string
}
interface ReplayProgress {
  compared: number
  matches: number
  divergences: number
  done: number
  total: number
}
interface ReplaySummary {
  task: string
  compared: number
  matches: number
  match_pct: number
  divergences: Divergence[]
  divergence_count: number
  stopped: boolean
  reconstructed: Record<string, string>
  comparable: string[]
  degraded: Record<string, string[]>
  change_drivers: ChangeDriver[]
}

type LaneStatus = 'idle' | 'running' | 'done' | 'error' | 'stopped'

const DECISION_CLS: Record<string, string> = {
  PASS: 'text-green-400',
  CLAMP: 'text-dam-blue',
  REJECT: 'text-red-400',
  FAULT: 'text-yellow-400',
}
function decisionCls(d: string): string {
  return DECISION_CLS[d] ?? (d.startsWith('ERROR') ? 'text-red-400' : 'text-dam-muted')
}

function StatTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded border border-dam-border/50 bg-dam-surface-2 px-2 py-1.5 text-center">
      <p className="text-[9px] uppercase tracking-widest text-dam-muted/70">{label}</p>
      <p className={`mt-0.5 font-mono text-sm font-bold ${tone ?? 'text-dam-text'}`}>{value}</p>
    </div>
  )
}

function GuardChip({ g }: { g: ReplayGuard }) {
  return (
    <span
      title={g.reason || `${g.name} ${g.decision}`}
      className="inline-flex items-center gap-1 rounded border border-dam-border/60 bg-dam-surface-1 px-1.5 py-0.5 text-[10px] font-mono"
    >
      <span className="text-dam-muted/60">L{g.layer}</span>
      <span className="text-dam-text">{g.name}</span>
      <span className={`font-bold ${decisionCls(g.decision)}`}>{g.decision}</span>
    </span>
  )
}

function ReplayLane({
  laneIndex,
  mcap,
  stacks,
  runSignal,
  stopSignal,
}: {
  laneIndex: number
  mcap: string
  stacks: string[]
  runSignal: number
  stopSignal: number
}) {
  const [stack, setStack] = useState(LIVE)
  const [status, setStatus] = useState<LaneStatus>('idle')
  const [progress, setProgress] = useState<ReplayProgress | null>(null)
  const [summary, setSummary] = useState<ReplaySummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recent, setRecent] = useState<Divergence[]>([])

  const esRef = useRef<EventSource | null>(null)
  const jobRef = useRef<string | null>(null)

  const cleanup = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
  }, [])

  useEffect(() => cleanup, [cleanup])

  const start = useCallback(async () => {
    if (!mcap || status === 'running') return
    cleanup()
    setStatus('running')
    setError(null)
    setSummary(null)
    setProgress(null)
    setRecent([])
    try {
      const { job_id } = await api.startReplayJob(mcap, stack)
      jobRef.current = job_id
      const es = new EventSource(api.replayEventsUrl(job_id))
      esRef.current = es
      es.onmessage = (e) => {
        const ev = JSON.parse(e.data)
        if (ev.type === 'progress') {
          setProgress({
            compared: ev.compared,
            matches: ev.matches,
            divergences: ev.divergences,
            done: ev.done,
            total: ev.total,
          })
          if (ev.recorded !== ev.replayed) {
            setRecent((r) =>
              [
                {
                  cycle: ev.cycle,
                  recorded: ev.recorded,
                  replayed: ev.replayed,
                  guards: ev.guards ?? [],
                },
                ...r,
              ].slice(0, 200),
            )
          }
        } else if (ev.type === 'done') {
          setSummary(ev.summary)
          setStatus(ev.summary.stopped ? 'stopped' : 'done')
          cleanup()
        } else if (ev.type === 'error') {
          setError(ev.message)
          setStatus('error')
          cleanup()
        }
      }
      es.onerror = () => {
        setStatus((s) => (s === 'running' ? 'error' : s))
        if (esRef.current) setError((prev) => prev ?? 'stream disconnected')
        cleanup()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus('error')
    }
  }, [mcap, stack, status, cleanup])

  const stop = useCallback(async () => {
    if (jobRef.current && status === 'running') {
      try {
        await api.stopReplayJob(jobRef.current)
      } catch {
        /* ignore */
      }
    }
  }, [status])

  const startRef = useRef(start)
  startRef.current = start
  const stopRef = useRef(stop)
  stopRef.current = stop
  useEffect(() => {
    if (runSignal > 0) startRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runSignal])
  useEffect(() => {
    if (stopSignal > 0) stopRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopSignal])

  const pct = progress && progress.total > 0 ? (progress.done / progress.total) * 100 : 0
  const matchPct =
    summary?.match_pct ??
    (progress && progress.compared > 0 ? (progress.matches / progress.compared) * 100 : null)
  const changed = summary?.divergence_count ?? progress?.divergences ?? 0
  const drivers = summary?.change_drivers ?? []
  const degraded = summary ? Object.entries(summary.degraded) : []

  const statusBadge = {
    idle: 'text-dam-muted border-dam-border',
    running: 'text-dam-blue bg-blue-500/10 border-blue-500/20',
    done: 'text-green-400 bg-green-500/10 border-green-500/20',
    stopped: 'text-dam-orange bg-orange-500/10 border-orange-500/20',
    error: 'text-red-400 bg-red-500/10 border-red-500/20',
  }[status]

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-dam-border bg-dam-surface-1">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-dam-border/50 bg-dam-surface-2/50 px-3 py-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-dam-muted">
          Column {laneIndex + 1}
        </span>
        <select
          value={stack}
          onChange={(e) => setStack(e.target.value)}
          disabled={status === 'running'}
          className="min-w-0 flex-1 max-w-[14rem] truncate rounded border border-dam-border bg-dam-surface-2 px-2 py-1 text-[11px] text-dam-text disabled:opacity-50"
        >
          <option value={LIVE}>Live · .dam_stackfile.yaml</option>
          {stacks.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span
          className={`shrink-0 rounded border px-2 py-0.5 text-[9px] font-bold uppercase ${statusBadge}`}
        >
          {status}
        </span>
      </div>

      {/* Body — internally scrolls so the page never grows */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {status === 'running' && (
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] text-dam-muted">
              <span>{progress ? `${progress.done} / ${progress.total} cycles` : 'starting…'}</span>
              <span>{pct.toFixed(0)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-dam-surface-3">
              <div className="h-full bg-dam-blue transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}

        {(progress || summary) && (
          <div className="grid grid-cols-3 gap-2">
            <StatTile label="Compared" value={String(summary?.compared ?? progress?.compared ?? 0)} />
            <StatTile
              label="Same decision"
              value={matchPct == null ? '—' : `${matchPct.toFixed(1)}%`}
              tone={matchPct != null && matchPct < 100 ? 'text-dam-orange' : 'text-green-400'}
            />
            <StatTile
              label="Changed"
              value={String(changed)}
              tone={changed > 0 ? 'text-red-400' : 'text-dam-text'}
            />
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded border border-red-500/20 bg-red-500/5 p-2 text-[11px] text-red-400">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {/* Actionable: which guards to tune */}
        {summary && (
          <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2">
            <p className="mb-1.5 flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-dam-muted/70">
              <Wrench size={11} /> What to tune in this stackfile
            </p>
            {drivers.length === 0 ? (
              <p className="flex items-center gap-1.5 text-[11px] text-green-400">
                <CheckCircle2 size={12} /> Reproduces the recording — no guard changed any
                decision.
              </p>
            ) : (
              <div className="space-y-1.5">
                {drivers.map((d) => (
                  <div key={d.name} className="rounded bg-dam-surface-1 px-2 py-1.5">
                    <div className="flex items-center gap-2 text-[11px]">
                      <span className="text-dam-muted/60 font-mono text-[10px]">L{d.layer}</span>
                      <span className="flex-1 truncate font-mono font-bold text-dam-text">
                        {d.name}
                      </span>
                      {d.decisions.map((dec) => (
                        <span key={dec} className={`font-mono font-bold ${decisionCls(dec)}`}>
                          {dec}
                        </span>
                      ))}
                      <span className="font-mono text-dam-muted">×{d.count}</span>
                    </div>
                    {d.sample_reason && (
                      <p className="mt-0.5 truncate text-[10px] text-dam-muted" title={d.sample_reason}>
                        {d.sample_reason}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Fidelity caveats — why a replay may legitimately differ */}
        {summary && (
          <div className="rounded border border-dam-border/40 bg-dam-surface-2/60 p-2 text-[10px] leading-relaxed">
            <p className="text-dam-muted">
              task <span className="font-mono text-dam-text">{summary.task}</span> · obs{' '}
              <span className="font-mono text-dam-text">
                {Object.entries(summary.reconstructed)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(' ')}
              </span>
            </p>
            {degraded.length > 0 && (
              <p className="mt-0.5 text-dam-orange">
                degraded (missing inputs):{' '}
                <span className="font-mono">
                  {degraded.map(([n, r]) => `${n} (${r.join('; ')})`).join(', ')}
                </span>{' '}
                — record these channels for a faithful replay.
              </p>
            )}
          </div>
        )}

        {/* Per-cycle decision changes with the guards that fired */}
        {recent.length > 0 && (
          <div className="space-y-1">
            <p className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Decision changes · recorded → replay ({recent.length})
            </p>
            <div className="space-y-1">
              {recent.map((d, i) => (
                <div
                  key={`${d.cycle}-${i}`}
                  className="rounded border border-dam-border/40 bg-dam-surface-2/50 px-2 py-1.5"
                >
                  <div className="flex items-center gap-2 text-[11px] font-mono">
                    <span className="text-dam-muted">#{d.cycle}</span>
                    <span className={decisionCls(d.recorded)}>{d.recorded}</span>
                    <span className="text-dam-muted/50">→</span>
                    <span className={`font-bold ${decisionCls(d.replayed)}`}>{d.replayed}</span>
                  </div>
                  {d.guards.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {d.guards.map((g, gi) => (
                        <GuardChip key={`${g.name}-${gi}`} g={g} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {status === 'idle' && !progress && (
          <div className="flex h-full items-center justify-center px-4 text-center">
            <p className="text-[11px] text-dam-muted/60">
              Pick a stackfile above, then <span className="text-dam-text">Replay all</span> to
              recompute every recorded cycle and see which guards would decide differently.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function ReplayContent() {
  const [sessions, setSessions] = useState<McapSessionSummary[]>([])
  const [stacks, setStacks] = useState<string[]>([])
  const [session, setSession] = useState('')
  const [laneCount, setLaneCount] = useState(2)
  const [runSignal, setRunSignal] = useState(0)
  const [stopSignal, setStopSignal] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.allSettled([api.listMcapSessions(), api.listStackfiles()])
      .then(([s, l]) => {
        if (s.status === 'fulfilled') setSessions(s.value.sessions ?? [])
        if (l.status === 'fulfilled') setStacks((l.value.entries ?? []).map((e) => e.name))
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-3">
      {/* Compact control bar — single row, no page growth */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-dam-border bg-dam-surface-1 px-3 py-2">
        <label className="flex items-center gap-2">
          <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
            Recorded MCAP
          </span>
          <select
            value={session}
            onChange={(e) => setSession(e.target.value)}
            className="min-w-[18rem] rounded border border-dam-border bg-dam-surface-2 px-2 py-1.5 text-xs text-dam-text"
          >
            <option value="">Select a session…</option>
            {sessions.map((s) => (
              <option key={s.filename} value={s.filename}>
                {s.filename} ({s.size_mb.toFixed(1)} MB)
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2">
          <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
            Columns
          </span>
          <div className="flex overflow-hidden rounded border border-dam-border">
            {[2, 3].map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setLaneCount(n)}
                className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                  laneCount === n
                    ? 'bg-dam-blue/15 text-dam-blue'
                    : 'bg-dam-surface-2 text-dam-muted hover:text-dam-text'
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </label>

        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setRunSignal((n) => n + 1)}
            disabled={!session}
            className="flex items-center gap-1.5 rounded bg-dam-blue px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-dam-blue-bright disabled:opacity-40"
          >
            <Play size={12} /> Replay all
          </button>
          <button
            onClick={() => setStopSignal((n) => n + 1)}
            className="flex items-center gap-1.5 rounded border border-dam-border bg-dam-surface-2 px-3 py-1.5 text-xs font-bold text-dam-muted transition-colors hover:border-dam-orange/40 hover:text-dam-orange"
          >
            <Square size={12} /> Stop
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-dam-muted">
          <Loader2 size={18} className="animate-spin" /> Loading sessions…
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 gap-3">
          {Array.from({ length: laneCount }, (_, i) => i).map((i) => (
            <ReplayLane
              key={i}
              laneIndex={i}
              mcap={session}
              stacks={stacks}
              runSignal={runSignal}
              stopSignal={stopSignal}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ReplayPage() {
  return (
    <PageShell
      title="Replay Comparison"
      subtitle="Recompute recorded cycles against a stackfile — see which guards would decide differently and what to tune"
    >
      <ReplayContent />
    </PageShell>
  )
}
