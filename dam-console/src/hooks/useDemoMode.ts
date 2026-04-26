'use client'
/**
 * useDemoMode
 *
 * Launches the real Python / Docker backend, then polls until it responds.
 * On launch it sends the current YAML stackfile (from localStorage) so the
 * backend starts with the user's exact configuration rather than the default.
 *
 * After the backend comes online, `readyToStart` is set to true for one
 * render cycle so the dashboard can auto-start the runtime cycle loop.
 */
import { useState, useCallback, useRef } from 'react'

export type LaunchMethod = 'python' | 'compose'

export interface DemoModeResult {
  starting: boolean
  launchError: string | null
  /** True for one tick immediately after backend first comes online */
  readyToStart: boolean
  launch: (method?: LaunchMethod) => Promise<void>
  clearError: () => void
  clearReady: () => void
}

const PYTHON_API  = '/api/system/launch'
const COMPOSE_API = '/api/system/compose-up'
const BACKEND_POLL_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080') + '/api/control/status'
const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS  = 90_000

export function useDemoMode(): DemoModeResult {
  const [starting, setStarting]         = useState(false)
  const [launchError, setLaunchError]   = useState<string | null>(null)
  const [readyToStart, setReadyToStart] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current)
      pollTimer.current = null
    }
  }, [])

  const waitForBackend = useCallback((): Promise<void> => {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + POLL_TIMEOUT_MS
      const attempt = async () => {
        if (Date.now() > deadline) {
          reject(new Error('Backend did not come online within 90 s'))
          return
        }
        try {
          const res = await fetch(BACKEND_POLL_URL, { signal: AbortSignal.timeout(2000) })
          if (res.ok) { resolve(); return }
        } catch {
          // still starting — keep polling
        }
        pollTimer.current = setTimeout(attempt, POLL_INTERVAL_MS)
      }
      attempt()
    })
  }, [])

  const launch = useCallback(async (method: LaunchMethod = 'python') => {
    setStarting(true)
    setLaunchError(null)
    setReadyToStart(false)
    try {
      // Read adapter and YAML from saved config
      let adapter = 'simulation'
      let yaml = ''
      try {
        const raw = localStorage.getItem('dam_config_v1')
        if (raw) adapter = (JSON.parse(raw) as { adapter?: string }).adapter ?? 'simulation'
        yaml = localStorage.getItem('dam_yaml_v1') ?? ''
      } catch { /* ignore */ }

      const url = method === 'compose' ? COMPOSE_API : PYTHON_API
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ adapter, yaml }),
      })
      const body = await res.json() as { ok: boolean; error?: string }
      if (!body.ok) throw new Error(body.error ?? 'Launch failed')

      await waitForBackend()
      // Signal the dashboard to auto-start the cycle loop
      setReadyToStart(true)
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }, [waitForBackend])

  const clearError = useCallback(() => setLaunchError(null), [])
  const clearReady = useCallback(() => setReadyToStart(false), [])

  return { starting, launchError, readyToStart, launch, clearError, clearReady }
}
