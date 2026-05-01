'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import type { CycleEvent, LogEntry, PerfSnapshot, TelemetrySnapshot } from '@/lib/types'

const MAX_LATENCY = 60
const MAX_EVENTS = 1000
const WINDOW_MS = 60000
const THROTTLE_MS = 100  // 10 Hz — halves re-render rate vs original 20 Hz

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
const gProcessedIds = new Set<number>()
let gCycleTimes: number[] = []
let gRejectTimes: number[] = []
let gClampTimes: number[] = []
let gGuardMap: Record<string, any> = {}
let gLiveImages: Record<string, Blob> = {}
let gActiveCameras: string[] = []
// Version counter replaces the shared gDirty boolean.
// Each hook instance compares its own lastVersion against gVersion, so
// multiple consumers (e.g. PageShell + Dashboard) update independently
// without racing for a shared flag.
let gVersion = 0
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
  gActiveCameras = []
  gLatency = []
  gLatencyCycleIds = []
  gCycleTimes = []
  gRejectTimes = []
  gClampTimes = []
  gProcessedIds.clear()
  gVersion++ // signal all hook instances to re-render with cleared state
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
    activeCameras: [...gActiveCameras],
    liveImages: { ...gLiveImages },
  }))

  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Per-instance version tracking — avoids the shared-flag race between
  // multiple useTelemetry consumers (e.g. PageShell and Dashboard).
  const lastVersionRef = useRef(gVersion)
  // Prevent onclose from scheduling a reconnect after the component unmounts.
  const mountedRef = useRef(true)

  const fetchHistory = useCallback(async () => {
    if (gHistoryFetched) return
    try {
      const data = await api.getRiskLog({ limit: 500 })
      if (data.events) {
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
        historyEntries.sort((a, b) => b.timestamp - a.timestamp)

        const existingMsg = new Set(gEvents.map(e => `${e.timestamp}:${e.message}`))
        const uniqueNew = historyEntries.filter(e => !existingMsg.has(`${e.timestamp}:${e.message}`))

        gEvents = [...gEvents, ...uniqueNew].sort((a, b) => b.timestamp - a.timestamp).slice(0, MAX_EVENTS)
        gHistoryFetched = true
        gVersion++
        setState(s => ({ ...s, events: [...gEvents] }))
      }
    } catch {}
  }, [])

  const connect = useCallback(() => {
    if (globalThis.window === undefined) return
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8080'
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(`${wsUrl}/ws/telemetry`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      gWsConnected = true
      gVersion++
      setState(s => ({ ...s, connected: true }))
      fetchHistory()

      api.listBoundaries().then(resp => {
        if (resp.boundaries) {
          for (const b of resp.boundaries) {
            if (b.nodes?.[0]?.constraint) {
              gGuardMap[b.name] = {
                name: b.name,
                layer: b.layer || 'L1',
                decision: 'STANDBY',
                reason: '',
              }
            }
          }
          gVersion++
          setState(s => ({ ...s, guardMap: { ...gGuardMap } }))
        }
      }).catch(() => {})

      const lastMsg = gEvents[0]?.message || ''
      if (lastMsg.includes('System Online')) {
        // Skip
      } else {
        gEvents = [{
          type: 'info' as const,
          message: 'System Online — Monitoring safety pipeline...',
          timestamp: Date.now() / 1000
        }, ...gEvents].slice(0, MAX_EVENTS)
      }

      gVersion++
      setState(s => ({ ...s, connected: true, events: [...gEvents] }))
    }

    ws.onmessage = (e: MessageEvent) => {
      if (typeof e.data !== 'string') {
        // Binary Protocol v2: [magic: 0x02][cycle_id: 4][name_len: 1][name][jpeg]
        try {
          const buf = e.data as ArrayBuffer
          const view = new Uint8Array(buf)
          const dataView = new DataView(buf)
          const magic = view[0]

          let cycleId = -1
          let nameLen = 0
          let nameOffset = 0

          if (magic === 0x02) {
            // v2: with cycle_id (4-byte Uint32, big-endian)
            cycleId = dataView.getUint32(1, false)
            nameLen = view[5]
            nameOffset = 6
          } else if (magic === 0x01) {
            // v1: legacy
            nameLen = view[1]
            nameOffset = 2
          } else {
            return // Unknown protocol
          }

          // Sync check: ignore images from older cycles
          if (cycleId !== -1 && gLatestCycle && cycleId < gLatestCycle.cycle_id) {
            return
          }

          const name = new TextDecoder().decode(view.subarray(nameOffset, nameOffset + nameLen))
          const jpegData = view.subarray(nameOffset + nameLen)
          gLiveImages[name] = new Blob([jpegData], { type: 'image/jpeg' })

          if (gActiveCameras.includes(name)) {
            // Already tracked
          } else {
            gActiveCameras = [...gActiveCameras, name]
          }
          gVersion++
        } catch (err) { console.error('Binary parse error:', err) }
        return
      }

      try {
        const msg = JSON.parse(e.data as string)

        // --- System Event Bridge: Notify other hooks ---
        if (msg.type === 'system_status' || msg.type === 'config_updated') {
          globalThis.dispatchEvent(new CustomEvent('dam-system-update', { detail: msg }))
          if (msg.type === 'system_status' && msg.message) {
            gEvents = [{
              type: 'info' as const,
              message: msg.message,
              timestamp: Date.now() / 1000
            }, ...gEvents].slice(0, MAX_EVENTS)
            gVersion++
          }
        }

        if (msg.type !== 'cycle') return
        const cycle = msg as CycleEvent
        const now = Date.now()

        // Keep gActiveCameras in sync. We merge current cameras with cycle-reported
        // ones and track the last time we saw a frame to avoid flapping.
        const cycleCams = cycle.active_cameras ?? []
        for (const cam of cycleCams) {
          if (!gActiveCameras.includes(cam)) {
            gActiveCameras = [...gActiveCameras, cam]
          }
        }

        const isNewer = cycle.cycle_id > (gLatestCycle?.cycle_id ?? -1)
        if (isNewer) {
          gLatestCycle = cycle
          if (cycle.perf != null) gLatestPerf = cycle.perf
          gVersion++

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
      setState(s => ({ ...s, connected: false }))
      // Only schedule reconnect while the component is still mounted.
      if (mountedRef.current) {
        timerRef.current = setTimeout(connect, 3000)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    connect()

    const doTick = () => {
      const now = Date.now()
      const cutoff = now - WINDOW_MS

      // Only prune window arrays when the oldest entry has expired
      if (gCycleTimes.length > 0 && gCycleTimes[0] <= cutoff) {
        gCycleTimes = gCycleTimes.filter(t => t > cutoff)
        gRejectTimes = gRejectTimes.filter(t => t > cutoff)
        gClampTimes = gClampTimes.filter(t => t > cutoff)
        gVersion++
      }

      // Each instance tracks its own lastVersion so consumers don't race.
      if (lastVersionRef.current === gVersion) return
      lastVersionRef.current = gVersion

      setState(s => {
        const next: TelemetrySnapshot = { ...s }
        let changed = false

        if (s.lastCycle !== gLatestCycle) {
          next.lastCycle = gLatestCycle
          changed = true
        }
        if (s.latestPerf !== gLatestPerf) {
          next.latestPerf = gLatestPerf
          changed = true
        }
        if (s.totalCycles !== gBuffer.totalCycles) {
          next.totalCycles = gBuffer.totalCycles
          changed = true
        }
        if (s.totalRejects !== gBuffer.totalRejects) {
          next.totalRejects = gBuffer.totalRejects
          changed = true
        }
        if (s.totalClamps !== gBuffer.totalClamps) {
          next.totalClamps = gBuffer.totalClamps
          changed = true
        }
        if (s.totalFaults !== gBuffer.totalFaults) {
          next.totalFaults = gBuffer.totalFaults
          changed = true
        }
        if (s.windowCycles !== gCycleTimes.length) {
          next.windowCycles = gCycleTimes.length
          changed = true
        }
        if (s.windowRejects !== gRejectTimes.length) {
          next.windowRejects = gRejectTimes.length
          changed = true
        }
        if (s.windowClamps !== gClampTimes.length) {
          next.windowClamps = gClampTimes.length
          changed = true
        }

        // Arrays/Objects need explicit check or just clone if we know version changed
        // To be safe and fast, we check if the global array is different from state
        if (s.events !== gEvents) {
          next.events = [...gEvents]
          changed = true
        }
        if (s.latencyHistory.length !== gLatency.length || s.latencyHistory[s.latencyHistory.length-1] !== gLatency[gLatency.length-1]) {
          next.latencyHistory = [...gLatency]
          next.latencyCycleIds = [...gLatencyCycleIds]
          changed = true
        }
        if (s.activeCameras !== gActiveCameras) {
          next.activeCameras = [...gActiveCameras]
          changed = true
        }
        if (s.liveImages !== gLiveImages) {
          next.liveImages = { ...gLiveImages }
          changed = true
        }
        if (s.guardMap !== gGuardMap) {
          next.guardMap = { ...gGuardMap }
          changed = true
        }

        return changed ? next : s
      })
    }

    // Run once immediately
    doTick()

    refreshTimerRef.current = setInterval(doTick, THROTTLE_MS)

    return () => {
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { ...state, reconnect: connect, reset: resetGlobalState }
}
