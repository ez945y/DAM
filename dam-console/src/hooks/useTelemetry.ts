'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import type { CycleEvent, LogEntry, PerfSnapshot, TelemetrySnapshot } from '@/lib/types'

const MAX_LATENCY = 60
const MAX_EVENTS = 1000
const WINDOW_MS = 60000 // 1 minute window
const THROTTLE_MS = 500 // 0.5s refresh rate

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
let gCycleTimes: number[] = []
let gRejectTimes: number[] = []
let gClampTimes: number[] = []
let gGuardMap: Record<string, any> = {}
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
  gLatency = []
  gLatencyCycleIds = []
  gCycleTimes = []
  gRejectTimes = []
  gClampTimes = []
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
    wsRef.current = ws

    ws.onopen = () => {
      gWsConnected = true
      void fetchHistory()
      
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
      try {
        const cycle = JSON.parse(e.data as string) as CycleEvent
        if (cycle.type !== 'cycle') return
        const now = Date.now()

        gLatestCycle = cycle
        if (cycle.perf != null) gLatestPerf = cycle.perf
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
      } catch (err) { console.error(err) }
    }

    ws.onclose = () => {
      wsRef.current = null
      gWsConnected = false
      // Clear live metrics on disconnect so charts don't show stale data.
      gLatency = []
      gLatencyCycleIds = []
      gLatestPerf = null
      setState(s => ({ ...s, connected: false, latencyHistory: [], latencyCycleIds: [], latestPerf: null }))
      timerRef.current = setTimeout(connect, 3000)
    }
  }, [])

  useEffect(() => {
    connect()
    refreshTimerRef.current = setInterval(() => {
      const now = Date.now()
      const cutoff = now - WINDOW_MS
      
      gCycleTimes = gCycleTimes.filter(t => t > cutoff)
      gRejectTimes = gRejectTimes.filter(t => t > cutoff)
      gClampTimes = gClampTimes.filter(t => t > cutoff)

      if (!gLatestCycle) return

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
    }, THROTTLE_MS)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { ...state, reconnect: connect, reset: resetGlobalState }
}
