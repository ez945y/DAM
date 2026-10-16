import { act, fireEvent, render, screen } from '@testing-library/react'
import { McapSessionList } from '@/components/McapSessionList'
import { api } from '@/lib/api'

jest.mock('@/lib/api', () => ({ api: {
  listMcapSessions: jest.fn(), getMcapSession: jest.fn(),
  archiveMcapSessions: jest.fn(), deleteMcapSession: jest.fn(),
  mcapDownloadUrl: (file: string) => `/download/${file}`,
} }))
const mockApi = api as jest.Mocked<typeof api>
const session = (filename: string) => ({ filename, size_bytes: 100, size_mb: 0.1, created_at: 1 })

beforeEach(() => {
  jest.clearAllMocks()
  mockApi.getMcapSession.mockRejectedValue(new Error('No details'))
})

it('removes successful deletions and keeps failed entries selectable for retry', async () => {
  mockApi.listMcapSessions.mockResolvedValueOnce({ sessions: [session('a.mcap'), session('b.mcap')] })
    .mockResolvedValue({ sessions: [session('b.mcap')] })
  mockApi.archiveMcapSessions.mockResolvedValue({
    success: false,
    archived: [{ filename: 'a.mcap', archived_filename: 'a.mcap', archived_path: '' }],
    failed: ['b.mcap'],
  })
  const onDelete = jest.fn()
  let view!: ReturnType<typeof render>
  await act(async () => { view = render(<McapSessionList onDeleteSession={onDelete} />) })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }))
  expect(screen.getByText(/This action is permanent/)).toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
  expect(onDelete).toHaveBeenCalledTimes(1)
  expect(onDelete).toHaveBeenCalledWith('a.mcap')
  expect(screen.queryByRole('button', { name: 'a.mcap' })).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: 'Select b.mcap' })).toBeChecked()
  expect(screen.getByRole('alert')).toHaveTextContent('Failed to delete: b.mcap')
  expect(screen.getByRole('button', { name: 'Delete selected' })).toBeEnabled()
  expect(view.container.querySelector('button button')).toBeNull()
})
