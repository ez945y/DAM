import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '@/lib/api'
import type { RuntimeStatus, BoundaryConfig } from '@/lib/types'

// ── Global Tracking State (Persists across component remounts) ──────
let gAccumulatedSec = 0
let gSegmentStart: number | null = null
let gStartedAt: number | null = null
let gLastKnownStatus: RuntimeStatus = {
  state: 'idle',
  backend_state: 'loading',
  cycle_count: 0,
  error: null,
  has_runtime: false,
  control_frequency_hz: undefined,
}

export function useRuntimeControl() {
  const [status, setStatus] = useState<RuntimeStatus>(gLastKnownStatus)
  const [boundaries, setBoundaries] = useState<BoundaryConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Cumulative running-time tracking ─────────────────────────────────────
  const [accumulatedSec, setAccumulatedSec] = useState(gAccumulatedSec)
  const [startedAt, setStartedAt] = useState<number | null>(gStartedAt)

  const prevRef = useRef(status.state)

  useEffect(() => {
    const curr = status.state
    const prev = prevRef.current
    prevRef.current = curr

    if (curr === 'running' && prev !== 'running') {
      // Entered running state
      if (gSegmentStart === null) {
        const now = Date.now()
        gSegmentStart = now
        gStartedAt = now
      }
      setStartedAt(gStartedAt)
    } else if (curr !== 'running' && prev === 'running') {
      // Left running state — bank elapsed seconds
      if (gSegmentStart !== null) {
        const elapsed = Math.floor((Date.now() - gSegmentStart) / 1000)
        gAccumulatedSec += elapsed
        gSegmentStart = null
      }
      setAccumulatedSec(gAccumulatedSec)

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
    refresh()
    refreshBoundaries()

    // 1. Reactive Update: Listen for backend events via WebSocket bridge
    const handleUpdate = (e: any) => {
      const msg = e.detail
      if (msg?.state || msg?.backend_state) {
        // Zero-latency update from Push
        const newStatus = { ...gLastKnownStatus, ...msg }
        gLastKnownStatus = newStatus
        setStatus(newStatus)
      }
      // No need to full refresh on every push unless explicitly requested
    }
    globalThis.addEventListener('dam-system-update', handleUpdate)

    // 2. Window Focus: Just re-trigger refresh if needed, but WS should be active
    const handleFocus = () => {
      // Optional: void refresh()
    }
    globalThis.addEventListener('focus', handleFocus)

    return () => {
      globalThis.removeEventListener('dam-system-update', handleUpdate)
      globalThis.removeEventListener('focus', handleFocus)
    }
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
    start:         () => {
      const hz = gLastKnownStatus.control_frequency_hz || 50
      const budget = Math.round(1000 / hz)
      return call(() => api.start({
        task_name: gLastKnownStatus.planned_task || gLastKnownStatus.available_tasks?.[0] || 'default',
        n_cycles: -1,
        cycle_budget_ms: budget
      }))
    },
    pause:         () => call(() => api.pause()),
    resume:        () => call(() => api.resume()),
    stop:          () => call(() => api.stop()),
    emergencyStop: () => call(() => api.emergencyStop()),
    reset:         () => call(() => api.reset()),
    confirmFault:  () => call(() => api.confirmFault()),
    recheckHardware: () => call(() => api.recheckHardware()),
  }
}
