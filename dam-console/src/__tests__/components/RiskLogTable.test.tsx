import { act, fireEvent, render, screen } from '@testing-library/react'
import { RiskLogTable } from '@/components/RiskLogTable'
import { api } from '@/lib/api'
import type { RiskEvent } from '@/lib/types'

const mockParams = new URLSearchParams()
const mockRouter = { push: jest.fn() }
jest.mock('next/navigation', () => ({
  useSearchParams: () => mockParams,
  useRouter: () => mockRouter,
}))
jest.mock('@/components/CycleSafetyInspector', () => ({
  CycleSafetyInspector: () => <div data-testid="safety-detail" />,
}))
jest.mock('@/lib/api', () => ({ api: {
  getRiskLog: jest.fn(), listMcapSessions: jest.fn(), getRiskLogFromMcap: jest.fn(),
  clearRiskLog: jest.fn(),
  exportRiskLogJsonUrl: () => '/export/json', exportRiskLogCsvUrl: () => '/export/csv',
} }))
const mockApi = api as jest.Mocked<typeof api>
const event = (id: number, cycleId = id): RiskEvent => ({
  event_id: id, cycle_id: cycleId, timestamp: 1, trace_id: `trace-${id}`,
  risk_level: 'CRITICAL', outcome: 'reject', was_rejected: true, was_clamped: false,
  fallback_triggered: null, guard_results: [], latency_ms: {},
})

beforeEach(() => {
  jest.clearAllMocks()
  localStorage.clear()
  for (const key of Array.from(mockParams.keys())) mockParams.delete(key)
  mockApi.listMcapSessions.mockResolvedValue({ sessions: [] })
})

it('ignores an older response after the user changes the outcome filter', async () => {
  let resolveOld!: (value: { events: RiskEvent[]; count: number }) => void
  mockApi.getRiskLog.mockImplementation(params => params?.outcome === 'reject'
    ? Promise.resolve({ events: [event(2)], count: 1 })
    : new Promise(resolve => { resolveOld = resolve }))
  await act(async () => { render(<RiskLogTable />) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reject' })) })
  expect(screen.getByText('#2')).toBeInTheDocument()
  await act(async () => { resolveOld({ events: [event(1)], count: 1 }) })
  expect(screen.getByText('#2')).toBeInTheDocument()
  expect(screen.queryByText('#1')).not.toBeInTheDocument()
  expect(screen.queryByText('syncing…')).not.toBeInTheDocument()
})

it('treats cycle zero as a valid deep link and expands its details', async () => {
  mockParams.set('cycle_id', '0')
  mockApi.getRiskLog.mockResolvedValue({ events: [event(7, 0)], count: 1 })
  jest.useFakeTimers()
  try {
    await act(async () => { render(<RiskLogTable />) })
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(screen.getByTestId('safety-detail')).toBeInTheDocument()
  } finally {
    jest.clearAllTimers()
    jest.useRealTimers()
  }
})
