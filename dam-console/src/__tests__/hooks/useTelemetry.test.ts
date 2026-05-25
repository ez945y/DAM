import { renderHook, act } from '@testing-library/react'
import { useTelemetry, resetGlobalState } from '@/hooks/useTelemetry'
import { api } from '@/lib/api'

// Mock WebSocket
class MockWebSocket {
  static readonly OPEN = 1
  readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close = jest.fn()
  send = jest.fn()

  constructor(public url: string) {
    MockWebSocket._instances.push(this)
    setTimeout(() => this.onopen?.(), 0)
  }

  static _instances: MockWebSocket[] = []
  static _reset() { MockWebSocket._instances = [] }
}

;(globalThis as unknown as Record<string, unknown>).WebSocket = MockWebSocket

describe('useTelemetry', () => {
  beforeEach(() => {
    resetGlobalState()
    MockWebSocket._reset()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('starts disconnected', () => {
    const { result } = renderHook(() => useTelemetry())
    expect(result.current.connected).toBe(false)
  })

  it('sets connected=true on open', async () => {
    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })
    expect(result.current.connected).toBe(true)
  })

  it('processes cycle event', async () => {
    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })

    const ws = MockWebSocket._instances[0]
    const cycleMsg = JSON.stringify({
      type: 'cycle',
      cycle_id: 5,
      trace_id: 'abc',
      was_clamped: false,
      was_rejected: false,
      risk_level: 'NORMAL',
      fallback_triggered: null,
      latency_ms: { total: 12.5 },
      guard_statuses: [{ name: 'MotionGuard', layer: 'L2', decision: 'PASS', reason: '' }],
      timestamp: 1700000000,
    })

    act(() => {
      ws.onmessage?.({ data: cycleMsg })
      jest.advanceTimersByTime(500)
    })

    expect(result.current.totalCycles).toBe(1)
    expect(result.current.guardMap['MotionGuard']).toBeDefined()
    expect(result.current.latencyHistory).toContain(12.5)
  })

  it('increments totalRejects on rejected cycle', async () => {
    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })

    const ws = MockWebSocket._instances[0]
    act(() => {
      ws.onmessage?.({ data: JSON.stringify({
        type: 'cycle', cycle_id: 1, trace_id: 'x', was_clamped: false, was_rejected: true,
        risk_level: 'CRITICAL', fallback_triggered: 'emergency_stop',
        latency_ms: { total: 5 },
        guard_statuses: [{ name: 'OODGuard', layer: 'L0', decision: 'REJECT', reason: 'ood' }],
        timestamp: 1700000001,
      }) })
      jest.advanceTimersByTime(500)
    })

    expect(result.current.totalRejects).toBe(1)
    expect(result.current.events.length).toBeGreaterThan(0)
  })

  it('schedules reconnect on close', async () => {
    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })

    const ws = MockWebSocket._instances[0]
    act(() => { ws.onclose?.() })
    expect(result.current.connected).toBe(false)

    // After 3s timer, should reconnect
    act(() => { jest.advanceTimersByTime(3100) })
    expect(MockWebSocket._instances.length).toBeGreaterThan(1)
  })

  it('ignores close events from a stale socket after remount', async () => {
    const first = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })
    expect(first.result.current.connected).toBe(true)

    const stale = MockWebSocket._instances[0]
    first.unmount()

    const second = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })
    expect(second.result.current.connected).toBe(true)

    act(() => { stale.onclose?.() })
    expect(second.result.current.connected).toBe(true)
  })

  it('drops stale boundaries from guardMap after reconnect with a smaller list', async () => {
    // Regression: Apply & Restart used to leave old boundaries (e.g. ood_welford)
    // visible on the home page because the listBoundaries handler only added
    // entries — never removed them.  The fix wholesale-replaces gGuardMap on
    // every onopen.
    const stub = jest.spyOn(api, 'listBoundaries')
      // First connection: two boundaries.
      .mockResolvedValueOnce({
        boundaries: [
          { name: 'ood_welford', layer: 'L0', type: 'single', nodes: [{ node_id: 'd', constraint: 'ood' }] } as any,
          { name: 'workspace',  layer: 'L1', type: 'single', nodes: [{ node_id: 'd', constraint: 'ws'  }] } as any,
        ],
      })
      // Second connection: only workspace remains (ood removed by user).
      .mockResolvedValueOnce({
        boundaries: [
          { name: 'workspace', layer: 'L1', type: 'single', nodes: [{ node_id: 'd', constraint: 'ws' }] } as any,
        ],
      })
    jest.spyOn(api, 'getStatus').mockResolvedValue({ cycle_count: 0 } as any)

    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers(); await Promise.resolve() })
    await act(async () => { await Promise.resolve() })

    expect(Object.keys(result.current.guardMap).sort()).toEqual(['ood_welford', 'workspace'])

    // Simulate a backend restart: socket closes, reconnect fires onopen again,
    // listBoundaries now returns a shorter list.
    const ws1 = MockWebSocket._instances[0]
    act(() => { ws1.onclose?.() })
    act(() => { jest.advanceTimersByTime(3100) })
    await act(async () => { jest.runOnlyPendingTimers(); await Promise.resolve() })
    await act(async () => { await Promise.resolve() })

    expect(Object.keys(result.current.guardMap)).toEqual(['workspace'])
    expect(result.current.guardMap['ood_welford']).toBeUndefined()

    stub.mockRestore()
  })

  it('ignores ping messages', async () => {
    const { result } = renderHook(() => useTelemetry())
    await act(async () => { jest.runOnlyPendingTimers() })

    const ws = MockWebSocket._instances[0]
    act(() => {
      ws.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
      jest.advanceTimersByTime(500)
    })
    expect(result.current.totalCycles).toBe(0)
  })
})
