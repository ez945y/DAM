'use client'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { PageShell } from '@/components/PageShell'
import { api } from '@/lib/api'
import type { McapSessionSummary } from '@/lib/api'
import { Play, Square, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react'

const LIVE = '__live__'

interface ReplayProgress {
  compared: number
  matches: number
  divergences: number
  done: number
  total: number
}

interface ReplaySummary {
  mcap: string
  stack: string
  task: string
  compared: number
  matches: number
  match_pct: number
  divergences: { cycle: number; recorded: string; replayed: string }[]
  divergence_count: number
  stopped: boolean
  reconstructed: Record<string, string>
  comparable: string[]
  degraded: Record<string, string[]>
}

type LaneStatus = 'idle' | 'running' | 'done' | 'error' | 'stopped'

function StatTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2 text-center">
      <p className="text-[9px] uppercase tracking-widest text-dam-muted">{label}</p>
      <p className={`mt-0.5 text-sm font-bold font-mono ${tone ?? 'text-dam-text'}`}>{value}</p>
    </div>
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
  const [recent, setRecent] = useState<{ cycle: number; recorded: string; replayed: string }[]>([])

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
              [{ cycle: ev.cycle, recorded: ev.recorded, replayed: ev.replayed }, ...r].slice(0, 50),
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
        // Stream ended (server closed after terminal event) — only an error if
        // we never reached a terminal state.
        setStatus((s) => (s === 'running' ? 'error' : s))
        if (esRef.current) {
          setError((prev) => prev ?? 'stream disconnected')
        }
        cleanup()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus('error')
    }
  }, [mcap, stack, status, cleanup])

  const stop = useCallback(async () => {
    if (jobRef.current && status === 'running') {
      try { await api.stopReplayJob(jobRef.current) } catch { /* ignore */ }
    }
  }, [status])

  // Parent "Run all" / "Stop all" signals.
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
    (progress && progress.compared > 0
      ? (progress.matches / progress.compared) * 100
      : null)

  const statusBadge = {
    idle: 'text-dam-muted border-dam-border',
    running: 'text-dam-blue bg-blue-500/10 border-blue-500/20',
    done: 'text-green-400 bg-green-500/10 border-green-500/20',
    stopped: 'text-dam-orange bg-orange-500/10 border-orange-500/20',
    error: 'text-red-400 bg-red-500/10 border-red-500/20',
  }[status]

  return (
    <div className="flex flex-col min-w-0 flex-1 rounded-lg border border-dam-border bg-dam-surface-1">
      <div className="flex items-center justify-between gap-2 border-b border-dam-border/50 bg-dam-surface-2/50 px-3 py-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-dam-muted">
          Lane {laneIndex + 1}
        </span>
        <span className={`px-2 py-0.5 rounded border text-[9px] font-bold uppercase ${statusBadge}`}>
          {status}
        </span>
      </div>

      <div className="space-y-2 p-3">
        <div className="space-y-1">
          <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
            Stackfile
          </span>
          <select
            value={stack}
            onChange={(e) => setStack(e.target.value)}
            disabled={status === 'running'}
            className="w-full bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5 disabled:opacity-50"
          >
            <option value={LIVE}>Live · .dam_stackfile.yaml</option>
            {stacks.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-2 pt-1">
          {status === 'running' ? (
            <button
              onClick={stop}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-dam-orange/10 border border-dam-orange/30 text-dam-orange text-xs font-bold rounded hover:bg-dam-orange/20 transition-colors"
            >
              <Square size={11} /> Stop
            </button>
          ) : (
            <button
              onClick={start}
              disabled={!mcap}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-dam-blue/10 border border-dam-blue/30 text-dam-blue text-xs font-bold rounded hover:bg-dam-blue/20 transition-colors disabled:opacity-40"
            >
              <Play size={11} /> Run
            </button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t border-dam-border/40 p-3 space-y-3">
        {status === 'running' && (
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] text-dam-muted">
              <span>{progress ? `${progress.done} / ${progress.total}` : 'starting…'}</span>
              <span>{pct.toFixed(0)}%</span>
            </div>
            <div className="h-1.5 bg-dam-surface-3 rounded overflow-hidden">
              <div className="h-full bg-dam-blue transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}

        {(progress || summary) && (
          <div className="grid grid-cols-3 gap-2">
            <StatTile
              label="Compared"
              value={String(summary?.compared ?? progress?.compared ?? 0)}
            />
            <StatTile
              label="Match"
              value={matchPct == null ? '—' : `${matchPct.toFixed(1)}%`}
              tone={matchPct != null && matchPct < 100 ? 'text-dam-orange' : 'text-green-400'}
            />
            <StatTile
              label="Diverged"
              value={String(summary?.divergence_count ?? progress?.divergences ?? 0)}
              tone={
                (summary?.divergence_count ?? progress?.divergences ?? 0) > 0
                  ? 'text-red-400'
                  : 'text-dam-text'
              }
            />
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded border border-red-500/20 bg-red-500/5 p-2 text-[11px] text-red-400">
            <AlertTriangle size={12} className="shrink-0 mt-0.5" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {summary && (
          <div className="rounded border border-dam-border/50 bg-dam-surface-2 p-2 space-y-1 text-[10px]">
            <p className="flex items-center gap-1 text-dam-muted">
              <CheckCircle2 size={11} /> task <span className="font-mono text-dam-text">{summary.task}</span>
            </p>
            <p className="text-dam-muted">
              comparable:{' '}
              <span className="font-mono text-dam-text">
                {summary.comparable.length ? summary.comparable.join(', ') : 'none'}
              </span>
            </p>
            {Object.keys(summary.degraded).length > 0 && (
              <p className="text-dam-orange">
                degraded:{' '}
                <span className="font-mono">
                  {Object.entries(summary.degraded)
                    .map(([n, r]) => `${n} (${r.join('; ')})`)
                    .join(', ')}
                </span>
              </p>
            )}
            <p className="text-dam-muted">
              obs:{' '}
              <span className="font-mono text-dam-text">
                {Object.entries(summary.reconstructed)
                  .map(([k, v]) => `${k}=${v}`)
                  .join('  ')}
              </span>
            </p>
          </div>
        )}

        {recent.length > 0 && (
          <div className="space-y-1">
            <p className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Divergences (recorded → replayed)
            </p>
            <div className="rounded border border-dam-border/50 overflow-hidden">
              <table className="w-full text-[10px]">
                <tbody className="divide-y divide-dam-border/30">
                  {recent.map((d, i) => (
                    <tr key={`${d.cycle}-${i}`} className="font-mono">
                      <td className="px-2 py-1 text-dam-muted">#{d.cycle}</td>
                      <td className="px-2 py-1 text-dam-text">{d.recorded}</td>
                      <td className="px-2 py-1 text-dam-muted">→</td>
                      <td className="px-2 py-1 text-dam-orange">{d.replayed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {status === 'idle' && !progress && (
          <p className="py-8 text-center text-[11px] text-dam-muted/60">
            Pick a session + stackfile, then Run to replay recorded cycles through these guards.
          </p>
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
    <div className="flex flex-col gap-4 min-h-[calc(100vh-160px)]">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-dam-border bg-dam-surface-1 p-3">
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
            MCAP session
          </span>
          <select
            value={session}
            onChange={(e) => setSession(e.target.value)}
            className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5 min-w-[20rem]"
          >
            <option value="">Select a session…</option>
            {sessions.map((s) => (
              <option key={s.filename} value={s.filename}>
                {s.filename} ({s.size_mb.toFixed(1)} MB)
              </option>
            ))}
          </select>
        </div>
        <p className="text-[10px] text-dam-muted max-w-xs">
          One session, replayed through 2–3 stackfiles side by side. Lane 1 defaults
          to the live config.
        </p>
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
            Lanes
          </span>
          <div className="flex rounded border border-dam-border overflow-hidden">
            {[2, 3].map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setLaneCount(n)}
                className={`px-3 py-1.5 text-xs font-medium transition-colors border-r border-dam-border last:border-r-0 ${
                  laneCount === n
                    ? 'bg-dam-blue/15 text-dam-blue'
                    : 'bg-dam-surface-2 text-dam-muted hover:text-dam-text'
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setRunSignal((n) => n + 1)}
            disabled={!session}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-dam-blue text-white text-xs font-bold rounded hover:bg-dam-blue-bright transition-colors disabled:opacity-40"
          >
            <Play size={12} /> Run all
          </button>
          <button
            onClick={() => setStopSignal((n) => n + 1)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs font-bold rounded hover:text-dam-orange hover:border-dam-orange/40 transition-colors"
          >
            <Square size={12} /> Stop all
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-20 text-dam-muted">
          <Loader2 size={18} className="animate-spin" /> Loading sessions…
        </div>
      ) : (
        <div className="flex flex-1 gap-4 min-h-0">
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
      title="Replay Through Guards"
      subtitle="Re-evaluate recorded sessions against different stackfiles"
    >
      <ReplayContent />
    </PageShell>
  )
}
