'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import type { CycleEvent, LogEntry, PerfSnapshot, TelemetrySnapshot } from '@/lib/types'

const MAX_LATENCY = 60
const MAX_EVENTS = 1000
const WINDOW_MS = 60000 // 1 minute window
const THROTTLE_MS = 100 // 0.1s refresh rate (10Hz) for smooth real-time feel

import { api } from '@/lib/api'

// ── Global State (persists across tab changes) ─────────────────────────────
const gBuffer = {
  totalCycles: 0,
  totalRejects: 0,
  totalClamps: 0,
  totalFaults: 0,
}
let gLatestCycle: CycleEvent | null = null
let gLatestPerf: PerfSnapshot | null = null
let gEvents: LogEntry[] = []
let gLatency: number[] = []
let gLatencyCycleIds: number[] = []
const gLastLogged = new Map<string, number>()
const gProcessedIds = new Set<number>() // Deduplication set
let gCycleTimes: number[] = []
let gRejectTimes: number[] = []
let gClampTimes: number[] = []
let gGuardMap: Record<string, any> = {}
let gLiveImages: Record<string, Blob> = {}
let gWsConnected = false
let gHistoryFetched = false

export const resetGlobalState = () => {
  gBuffer.totalCycles = 0
  gBuffer.totalRejects = 0
  gBuffer.totalClamps = 0
  gBuffer.totalFaults = 0
  gLatestCycle = null
  gLatestPerf = null
  gEvents = []
  gGuardMap = {}
  gLiveImages = {}
  gLatency = []
  gLatencyCycleIds = []
  gCycleTimes = []
  gRejectTimes = []
  gClampTimes = []
  gProcessedIds.clear()
}

export function useTelemetry(): TelemetrySnapshot & { reconnect: () => void, reset: () => void } {
  const [state, setState] = useState<TelemetrySnapshot>(() => ({
    connected: gWsConnected,
    lastCycle: gLatestCycle,
    guardMap: { ...gGuardMap },
    latencyHistory: [...gLatency],
    latencyCycleIds: [...gLatencyCycleIds],
    latestPerf: gLatestPerf,
    totalCycles: gBuffer.totalCycles,
    totalRejects: gBuffer.totalRejects,
    totalClamps: gBuffer.totalClamps,
    totalFaults: gBuffer.totalFaults,
    windowCycles: gCycleTimes.length,
    windowRejects: gRejectTimes.length,
    windowClamps: gClampTimes.length,
    events: [...gEvents],
  }))

  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchHistory = useCallback(async () => {
    if (gHistoryFetched) return
    try {
      const data = await api.getRiskLog({ limit: 500 })
      if (!data.events) return

      const historyEntries: LogEntry[] = []
      data.events.forEach(ev => {
        ev.guard_results.forEach(g => {
          if (g.decision !== 'PASS') {
            historyEntries.push({
              type: g.decision,
              message: `[${g.layer}] ${g.name}: ${g.reason || g.decision}`,
              timestamp: ev.timestamp
            })
          }
        })
      })
      // Sort descending by timestamp
      historyEntries.sort((a, b) => b.timestamp - a.timestamp)

      // Merge with de-duplication
      const existingMsg = new Set(gEvents.map(e => `${e.timestamp}:${e.message}`))
      const uniqueNew = historyEntries.filter(e => !existingMsg.has(`${e.timestamp}:${e.message}`))

      gEvents = [...gEvents, ...uniqueNew].sort((a, b) => b.timestamp - a.timestamp).slice(0, MAX_EVENTS)
      gHistoryFetched = true
      setState(s => ({ ...s, events: [...gEvents] }))
    } catch {}
  }, [])

  const connect = useCallback(() => {
    if (typeof window === 'undefined') return
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8080'
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(`${wsUrl}/ws/telemetry`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      gWsConnected = true
      setState(s => ({ ...s, connected: true }))
      fetchHistory()

      // Fetch boundaries that have valid callbacks defined
      api.listBoundaries().then(resp => {
        if (!resp.boundaries) return
        for (const b of resp.boundaries) {
          // Only add if it has a constraint (meaning it's an actual guard)
          if (b.nodes?.[0]?.constraint) {
            gGuardMap[b.name] = {
              name: b.name,
              layer: b.layer || 'L1',
              decision: 'STANDBY',
              reason: '',
            }
          }
        }
        setState(s => ({ ...s, guardMap: { ...gGuardMap } }))
      }).catch(() => {})

      const lastMsg = gEvents[0]?.message || ''
      const isOnlineMsg = lastMsg.includes('System Online')

      if (!isOnlineMsg) {
        gEvents = [{
          type: 'info' as const,
          message: 'System Online — Monitoring safety pipeline...',
          timestamp: Date.now() / 1000
        }, ...gEvents].slice(0, MAX_EVENTS)
      }

      setState(s => ({ ...s, connected: true, events: [...gEvents] }))
    }

    ws.onmessage = (e: MessageEvent) => {
      if (typeof e.data !== 'string') {
        // --- Binary Protocol: [magic: 0x01][name_len: 1][name][jpeg] ---
        try {
          const buf = e.data as ArrayBuffer
          const view = new Uint8Array(buf)
          if (view[0] === 0x01) {
            const nameLen = view[1]
            const name = new TextDecoder().decode(view.subarray(2, 2 + nameLen))
            const jpegData = view.subarray(2 + nameLen)
            const blob = new Blob([jpegData], { type: 'image/jpeg' })

            // Optimization: Update global map and trigger a partial state update if needed.
            // But usually, we just want this to be available for the next refersh timer tick.
            gLiveImages[name] = blob
          }
        } catch (err) { console.error('Binary parse error:', err) }
        return
      }

      try {
        const msg = JSON.parse(e.data as string)

        // --- System Event Bridge: Notify other hooks ---
        if (msg.type === 'system_status' || msg.type === 'config_updated') {
          window.dispatchEvent(new CustomEvent('dam-system-update', { detail: msg }))
          if (msg.type === 'system_status' && msg.message) {
            gEvents = [{
              type: 'info' as const,
              message: msg.message,
              timestamp: Date.now() / 1000
            }, ...gEvents].slice(0, MAX_EVENTS)
            setState(s => ({ ...s, events: [...gEvents] }))
          }
        }

        if (msg.type !== 'cycle') return
        const cycle = msg as CycleEvent
        const now = Date.now()

        // Attach live images from the binary buffer
        if (cycle.active_cameras) {
          const images: Record<string, string | Blob> = {}
          cycle.active_cameras.forEach(name => {
            if (gLiveImages[name]) {
              images[name] = gLiveImages[name]!
            }
          })
          cycle.live_images = images
        }

        gLatestCycle = cycle
        if (cycle.perf != null) gLatestPerf = cycle.perf

        const isNewCycle = !gProcessedIds.has(cycle.cycle_id)
        if (isNewCycle) {
          gProcessedIds.add(cycle.cycle_id)
          gBuffer.totalCycles++
          gCycleTimes.push(now)

          if (cycle.was_rejected) {
            gBuffer.totalRejects++
            gRejectTimes.push(now)
          }
          if (cycle.was_clamped) {
            gBuffer.totalClamps++
            gClampTimes.push(now)
          }

          let hasFault = false
          const logEntries: LogEntry[] = []
          const logNow = Date.now()
          for (const g of cycle.guard_statuses) {
            gGuardMap[g.name] = g
            if (g.decision === 'FAULT' || g.decision === 'REJECT' || g.decision === 'CLAMP') {
              if (g.decision === 'FAULT') hasFault = true
              const key = `${g.name}:${g.decision}`
              const lastLogged = gLastLogged.get(key) ?? 0
              if (logNow - lastLogged >= 2000) {
                gLastLogged.set(key, logNow)
                logEntries.push({
                  type: g.decision,
                  message: `[${g.layer}] ${g.name}: ${g.reason || g.decision}`,
                  timestamp: cycle.timestamp,
                })
              }
            }
          }
          if (hasFault) gBuffer.totalFaults++

          if (logEntries.length > 0) {
            gEvents = [...logEntries, ...gEvents].slice(0, MAX_EVENTS)
          }

          gLatency = [...gLatency, cycle.latency_ms['total'] || 0].slice(-MAX_LATENCY)
          gLatencyCycleIds = [...gLatencyCycleIds, cycle.cycle_id].slice(-MAX_LATENCY)
        }
      } catch (err) { console.error('WS JSON error:', err) }
    }

    ws.onclose = () => {
      wsRef.current = null
      gWsConnected = false
      // Keep latency/perf data visible so charts don't flash empty on reconnect.
      // Only connected: false so the UI can show a reconnecting indicator.
      setState(s => ({ ...s, connected: false }))
      timerRef.current = setTimeout(connect, 3000)
    }
  }, [])

  useEffect(() => {
    connect()
    // Immediate first tick to ensure UI shows data even if interval hasn't fired
    const doTick = () => {
      const now = Date.now()
      const cutoff = now - WINDOW_MS

      gCycleTimes = gCycleTimes.filter(t => t > cutoff)
      gRejectTimes = gRejectTimes.filter(t => t > cutoff)
      gClampTimes = gClampTimes.filter(t => t > cutoff)

      // Always update state, even if gLatestCycle is null (shows initialConnecting)
      setState(s => ({
        ...s,
        lastCycle: gLatestCycle,
        guardMap: { ...gGuardMap },
        latencyHistory: [...gLatency],
        latencyCycleIds: [...gLatencyCycleIds],
        latestPerf: gLatestPerf,
        totalCycles: gBuffer.totalCycles,
        totalRejects: gBuffer.totalRejects,
        totalClamps: gBuffer.totalClamps,
        totalFaults: gBuffer.totalFaults,
        windowCycles: gCycleTimes.length,
        windowRejects: gRejectTimes.length,
        windowClamps: gClampTimes.length,
        events: [...gEvents],
      }))
    }

    // Run once immediately
    doTick()

    // Then run at THROTTLE_MS intervals
    refreshTimerRef.current = setInterval(doTick, THROTTLE_MS)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { ...state, reconnect: connect, reset: resetGlobalState }
}
