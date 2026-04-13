import { render, screen, fireEvent } from '@testing-library/react'
import { ControlPanel } from '@/components/ControlPanel'

const defaultProps = {
  state: 'idle' as const,
  cycleCount: 0,
  error: null,
  loading: false,
  onStart: jest.fn(),
  onPause: jest.fn(),
  onResume: jest.fn(),
  onStop: jest.fn(),
  onEStop: jest.fn(),
  onReset: jest.fn(),
}

describe('ControlPanel', () => {
  beforeEach(() => jest.clearAllMocks())

  it('renders state badge', () => {
    render(<ControlPanel {...defaultProps} />)
    expect(screen.getByText('IDLE')).toBeInTheDocument()
  })

  it('calls onStart when Start clicked', () => {
    render(<ControlPanel {...defaultProps} />)
    fireEvent.click(screen.getByText('Start'))
    expect(defaultProps.onStart).toHaveBeenCalledTimes(1)
  })

  it('Start is disabled when running', () => {
    render(<ControlPanel {...defaultProps} state="running" />)
    expect(screen.getByText('Start')).toBeDisabled()
  })

  it('shows Pause when running', () => {
    render(<ControlPanel {...defaultProps} state="running" />)
    expect(screen.getByText(/Pause/)).toBeInTheDocument()
  })

  it('shows Resume when paused', () => {
    render(<ControlPanel {...defaultProps} state="paused" />)
    expect(screen.getByText(/Resume/)).toBeInTheDocument()
  })

  it('calls onEStop when E-STOP clicked', () => {
    render(<ControlPanel {...defaultProps} />)
    fireEvent.click(screen.getByText(/Emergency Stop/i))
    expect(defaultProps.onEStop).toHaveBeenCalledTimes(1)
  })

  it('renders error message', () => {
    render(<ControlPanel {...defaultProps} error="Connection refused" />)
    expect(screen.getByText('Connection refused')).toBeInTheDocument()
  })

  it('Stop is disabled when idle', () => {
    render(<ControlPanel {...defaultProps} state="idle" />)
    expect(screen.getByText('Stop')).toBeDisabled()
  })

  it('renders cycle count', () => {
    render(<ControlPanel {...defaultProps} cycleCount={1234} />)
    expect(screen.getByText('1,234')).toBeInTheDocument()
  })
})
