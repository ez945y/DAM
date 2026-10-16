import { act, fireEvent, render, screen } from '@testing-library/react'
import McapViewerPage from '@/app/mcap-viewer/page'
import { api, type McapCycle, type McapSessionDetail } from '@/lib/api'

const mockParams = new URLSearchParams()
const mockRouter = { replace: jest.fn() }
jest.mock('next/navigation', () => ({
  useSearchParams: () => mockParams,
  useRouter: () => mockRouter,
}))
jest.mock('@/components/PageShell', () => ({ PageShell: ({ children }: any) => <div>{children}</div> }))
jest.mock('@/components/McapTimelineView', () => ({
  McapTimelineView: ({ cycles }: { cycles: McapCycle[] }) => (
    <div data-testid="timeline">{cycles.map(c => c.cycle_id).join(',')}</div>
  ),
}))
jest.mock('@/components/McapCycleInspector', () => ({
  McapCycleInspector: ({ filename, cycleId }: any) => <div data-testid="cycle">{filename}:{cycleId}</div>,
}))
jest.mock('@/components/McapCameraPlayer', () => ({ McapCameraPlayer: () => null }))
jest.mock('@/lib/api', () => ({ api: {
  listMcapSessions: jest.fn(), getMcapSession: jest.fn(), listMcapCycles: jest.fn(),
  archiveMcapSessions: jest.fn(), deleteMcapSession: jest.fn(),
  mcapDownloadUrl: (file: string) => `/download/${file}`,
} }))
const mockApi = api as jest.Mocked<typeof api>
const session = (filename: string) => ({ filename, size_bytes: 100, size_mb: 0.1, created_at: 1 })
const cycle = (id: number): McapCycle => ({
  cycle_id: id, seq: id, timestamp_ns: id * 100, timestamp: id / 1e7,
  has_violation: false, has_clamp: false, violated_layer_mask: 0,
  clamped_layer_mask: 0, violated_layers: [], clamped_layers: [],
})

beforeEach(() => {
  jest.clearAllMocks()
  for (const key of Array.from(mockParams.keys())) mockParams.delete(key)
  mockParams.set('filename', 'a.mcap')
  mockParams.set('cycle_id', '0')
  mockApi.listMcapSessions.mockResolvedValue({ sessions: [session('a.mcap'), session('b.mcap')] })
  mockApi.getMcapSession.mockImplementation(async filename => ({
    ...session(filename), metadata: {}, stats: {
      total_cycles: 1, violation_cycles: 0, clamp_cycles: 0, duration_sec: 1,
      cameras: [], violated_layers: [], clamped_layers: [],
    },
  } as McapSessionDetail))
})

it('clears old cycles while loading another recording and handles cached cycle zero', async () => {
  let resolveB!: (value: { filename: string; count: number; cycles: McapCycle[] }) => void
  mockApi.listMcapCycles.mockImplementation((filename, since) => {
    if (filename === 'b.mcap') return new Promise(resolve => { resolveB = resolve })
    return Promise.resolve({ filename, count: since == null ? 1 : 0, cycles: since == null ? [cycle(0)] : [] })
  })
  await act(async () => { render(<McapViewerPage />) })
  expect(screen.getByTestId('cycle')).toHaveTextContent('a.mcap:0')
  fireEvent.click(screen.getByRole('button', { name: /b\.mcap/ }))
  expect(screen.getByTestId('timeline')).toBeEmptyDOMElement()
  expect(mockRouter.replace).toHaveBeenLastCalledWith(expect.not.stringContaining('cycle_id'), { scroll: false })
  await act(async () => { resolveB({ filename: 'b.mcap', count: 1, cycles: [cycle(99)] }) })
  expect(screen.getByTestId('cycle')).toHaveTextContent('b.mcap:99')
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /a\.mcap/ })) })
  expect(mockApi.listMcapCycles).toHaveBeenLastCalledWith('a.mcap', 0)
  expect(screen.getByTestId('timeline')).toHaveTextContent('0')
})

it('resets delete confirmation when selecting a different recording', async () => {
  mockApi.listMcapCycles.mockImplementation(async filename => ({ filename, count: 1, cycles: [cycle(0)] }))
  await act(async () => { render(<McapViewerPage />) })
  fireEvent.click(screen.getByTitle('Delete session permanently'))
  expect(screen.getByText(/This permanently deletes/)).toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /b\.mcap/ })) })
  expect(screen.queryByRole('button', { name: 'Delete permanently' })).not.toBeInTheDocument()
  expect(mockApi.deleteMcapSession).not.toHaveBeenCalled()
})

it('updates the selected recording after partial batch deletion', async () => {
  mockApi.listMcapCycles.mockImplementation(async filename => ({ filename, count: 1, cycles: [cycle(0)] }))
  mockApi.archiveMcapSessions.mockResolvedValue({
    success: false,
    archived: [{ filename: 'a.mcap', archived_filename: 'a.mcap', archived_path: '' }],
    failed: ['b.mcap'],
  })
  await act(async () => { render(<McapViewerPage />) })
  mockApi.listMcapSessions.mockResolvedValue({ sessions: [session('b.mcap')] })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
  expect(screen.queryByRole('checkbox', { name: 'Select a.mcap' })).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: 'Select b.mcap' })).toBeChecked()
  expect(screen.getByText('Failed to delete: b.mcap')).toBeInTheDocument()
  expect(screen.getByTestId('cycle')).toHaveTextContent('b.mcap:0')
})
