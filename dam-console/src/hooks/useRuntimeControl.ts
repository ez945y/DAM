import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '@/lib/api'
import type { RuntimeStatus, BoundaryConfig } from '@/lib/types'

// ── Global Tracking State (Persists across component remounts) ──────
let gAccumulatedSec = 0
let gSegmentStart: number | null = null
let gStartedAt: number | null = null
let gPrevState = 'idle'
let gLastKnownStatus: RuntimeStatus = {
  state: 'idle',
  cycle_count: 0,
  error: null,
  has_runtime: false,
}

export function useRuntimeControl() {
  const [status, setStatus] = useState<RuntimeStatus>(gLastKnownStatus)
  const [boundaries, setBoundaries] = useState<BoundaryConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Cumulative running-time tracking ─────────────────────────────────────
  const [accumulatedSec, setAccumulatedSec] = useState(gAccumulatedSec)
  const [startedAt, setStartedAt] = useState<number | null>(gStartedAt)

  useEffect(() => {
    const curr = status.state
    const prev = gPrevState
    gPrevState = curr

    if (curr === 'running' && prev !== 'running') {
      // Entered running state
      const now = Date.now()
      gSegmentStart = now
      gStartedAt = now
      setStartedAt(now)
    } else if (curr !== 'running' && prev === 'running') {
      // Left running state — bank elapsed seconds
      if (gSegmentStart !== null) {
        const elapsed = Math.floor((Date.now() - gSegmentStart) / 1000)
        gAccumulatedSec += elapsed
        gSegmentStart = null
        setAccumulatedSec(gAccumulatedSec)
      }
      if (curr === 'idle') {
        // Full reset on idle (reset or fresh stop)
        gAccumulatedSec = 0
        gStartedAt = null
        setAccumulatedSec(0)
        setStartedAt(null)
      }
    }
  }, [status.state])

  // ── API ────────────────────────────────────────────────────────────────
  const refreshBoundaries = useCallback(async () => {
    try {
      const resp = await api.listBoundaries()
      setBoundaries(resp.boundaries)
    } catch { /* ignore */ }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const s = await api.getStatus()
      gLastKnownStatus = s
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'API unavailable')
      const fallback = { ...gLastKnownStatus, state: 'idle' as const, active_task: null, active_boundaries: [] }
      gLastKnownStatus = fallback
      setStatus(fallback)
    }
  }, [])

  useEffect(() => {
    void refresh()
    void refreshBoundaries()
    const id = setInterval(() => { void refresh() }, 3000)
    return () => clearInterval(id)
  }, [refresh, refreshBoundaries])

  const call = useCallback(async (fn: () => Promise<unknown>) => {
    setLoading(true)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [refresh])

  return {
    status,
    loading,
    error,
    refresh,
    boundaries,
    /** Timestamp when the current running segment started (null if not running) */
    startedAt,
    /** Total seconds the runtime has been in "running" state this session */
    accumulatedSec,
    start:         () => call(() => api.start({ task_name: gLastKnownStatus.planned_task || gLastKnownStatus.available_tasks?.[0] || 'default', n_cycles: -1, cycle_budget_ms: 20 })),
    pause:         () => call(() => api.pause()),
    resume:        () => call(() => api.resume()),
    stop:          () => call(() => api.stop()),
    emergencyStop: () => call(() => api.emergencyStop()),
    reset:         () => call(() => api.reset()),
    recheckHardware: () => call(() => api.recheckHardware()),
  }
}
