'use client'
/**
 * useLiveMode — shared singleton for the camera live mode toggle.
 *
 * Both the Dashboard's camera panel and the MCAP Viewer share the same
 * live-mode flag.  When live mode is ON:
 *   - Camera images come from WebSocket telemetry (lastCycle.live_images)
 *   - The MCAP timeline / player are hidden
 *   - The camera player shows a realtime feed instead of MCAP frames
 *
 * State is stored in localStorage so it survives navigation between pages.
 */
import { useState, useEffect, useCallback } from 'react'

const LS_KEY = 'dam_live_mode_v1'

let _listeners: Array<(v: boolean) => void> = []
let _current: boolean = false

function _set(v: boolean) {
  _current = v
  try { localStorage.setItem(LS_KEY, JSON.stringify(v)) } catch { /* SSR */ }
  _listeners.forEach(fn => fn(v))
}

// Initialise from localStorage on first import (browser only)
if (typeof globalThis.window !== 'undefined') {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) _current = JSON.parse(raw) as boolean
  } catch { /* ignore */ }
}

export function useLiveMode(): { liveMode: boolean; toggleLiveMode: () => void; setLiveMode: (v: boolean) => void } {
  const [liveMode, setLocal] = useState<boolean>(_current)

  useEffect(() => {
    _listeners.push(setLocal)
    return () => { _listeners = _listeners.filter(fn => fn !== setLocal) }
  }, [])

  const setLiveMode = useCallback((v: boolean) => _set(v), [])
  const toggleLiveMode = useCallback(() => _set(!_current), [])

  return { liveMode, toggleLiveMode, setLiveMode }
}
