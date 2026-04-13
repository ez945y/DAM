import { renderHook, act } from '@testing-library/react'
import { useTelemetry, resetGlobalState } from '@/hooks/useTelemetry'

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1
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

;(global as unknown as Record<string, unknown>).WebSocket = MockWebSocket

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
