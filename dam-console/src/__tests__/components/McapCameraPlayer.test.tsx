import { act, render, screen } from '@testing-library/react'
import { McapCameraPlayer } from '@/components/McapCameraPlayer'
import { api, type McapFrame } from '@/lib/api'
import { gLiveImages } from '@/hooks/useTelemetry'

jest.mock('@/hooks/useTelemetry', () => ({ gLiveImages: {} }))
jest.mock('@/lib/api', () => ({ api: {
  listMcapFrames: jest.fn(),
  mcapFrameUrl: (file: string, cam: string, idx: number) => `/${file}/${cam}/${idx}`,
  mcapFrameAtUrl: (file: string, cam: string, ts: number) => `/${file}/${cam}?ts=${ts}`,
} }))

const listFrames = api.listMcapFrames as jest.MockedFunction<typeof api.listMcapFrames>
const frames: McapFrame[] = [100, 200, 300].map((ns, idx) => ({
  idx, log_time_ns: ns, timestamp: ns / 1e9,
}))
const frameResponse = (items: McapFrame[]) => ({ camera: 'top', count: items.length, frames: items })

beforeEach(() => {
  jest.clearAllMocks()
  for (const name of Object.keys(gLiveImages)) delete gLiveImages[name]
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: jest.fn(() => 'blob:frame') })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: jest.fn() })
})

it('opens at the frame nearest the requested cycle on initial load', async () => {
  listFrames.mockResolvedValue(frameResponse(frames))
  await act(async () => {
    render(<McapCameraPlayer filename="a.mcap" cameras={['top']} currentTimestampNs={290} />)
  })
  expect(screen.getByAltText('top frame 2')).toHaveAttribute('src', '/a.mcap/top/2')
  expect(screen.getByText('3/3')).toBeInTheDocument()
})

it('ignores a previous recording response that arrives after switching files', async () => {
  let resolveOld!: (value: ReturnType<typeof frameResponse>) => void
  listFrames.mockImplementation(file => file === 'a.mcap'
    ? new Promise(resolve => { resolveOld = resolve })
    : Promise.resolve(frameResponse(frames.slice(0, 1))))
  const { rerender } = render(<McapCameraPlayer filename="a.mcap" cameras={['top']} currentTimestampNs={null} />)
  await act(async () => {
    rerender(<McapCameraPlayer filename="b.mcap" cameras={['top']} currentTimestampNs={null} />)
  })
  await act(async () => { resolveOld(frameResponse(frames)) })
  expect(screen.getByText('1/1')).toBeInTheDocument()
  expect(screen.queryByText('1/3')).not.toBeInTheDocument()
  expect(screen.getByAltText('top frame 0')).toHaveAttribute('src', '/b.mcap/top/0')
})

it('does not duplicate an in-flight camera request on parent rerender', async () => {
  let resolve!: (value: ReturnType<typeof frameResponse>) => void
  listFrames.mockReturnValue(new Promise(r => { resolve = r }))
  const { rerender } = render(<McapCameraPlayer filename="a.mcap" cameras={['top']} currentTimestampNs={null} />)
  rerender(<McapCameraPlayer filename="a.mcap" cameras={['top']} currentTimestampNs={200} />)
  expect(listFrames).toHaveBeenCalledTimes(1)
  await act(async () => { resolve(frameResponse(frames)) })
})

it('displays an already cached live frame without waiting for another event', () => {
  gLiveImages.top = new Blob(['frame'])
  const { unmount } = render(<McapCameraPlayer filename="" cameras={['top']} currentTimestampNs={null} liveMode />)
  expect(screen.getByAltText('top live')).toHaveAttribute('src', 'blob:frame')
  expect(screen.queryByText('Waiting for frames…')).not.toBeInTheDocument()
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:frame')
})

it('displays the first live frame when it arrives after mounting', () => {
  render(<McapCameraPlayer filename="" cameras={['top']} currentTimestampNs={null} liveMode />)
  expect(screen.getByText('Waiting for frames…')).toBeInTheDocument()
  act(() => {
    gLiveImages.top = new Blob(['frame'])
    globalThis.dispatchEvent(new CustomEvent('dam-live-frame', { detail: 'top' }))
  })
  expect(screen.getByAltText('top live')).toHaveAttribute('src', 'blob:frame')
  expect(screen.getByAltText('top live')).not.toHaveClass('hidden')
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
})
