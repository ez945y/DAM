import { render, screen, fireEvent } from '@testing-library/react'
import { EventLog } from '@/components/EventLog'
import type { LogEntry } from '@/lib/types'

const entries: LogEntry[] = [
  { type: 'REJECT', message: 'OOD detected in L0', timestamp: 1700000000 },
  { type: 'CLAMP', message: 'Velocity clamped', timestamp: 1700000001 },
  { type: 'info', message: 'WebSocket connected', timestamp: 1700000002 },
  { type: 'FAULT', message: 'Hardware fault', timestamp: 1700000003 },
]

describe('EventLog', () => {
  it('renders empty state', () => {
    render(<EventLog entries={[]} />)
    expect(screen.getByText('No events')).toBeInTheDocument()
  })

  it('renders all entries by default', () => {
    render(<EventLog entries={entries} />)
    expect(screen.getByText('OOD detected in L0')).toBeInTheDocument()
    expect(screen.getByText('WebSocket connected')).toBeInTheDocument()
  })

  it('filters to REJECT only', () => {
    render(<EventLog entries={entries} />)
    fireEvent.click(screen.getByText('REJECT'))
    expect(screen.getByText('OOD detected in L0')).toBeInTheDocument()
    expect(screen.queryByText('WebSocket connected')).not.toBeInTheDocument()
  })

  it('filters to FAULT only', () => {
    render(<EventLog entries={entries} />)
    fireEvent.click(screen.getByText('FAULT'))
    expect(screen.getByText('Hardware fault')).toBeInTheDocument()
    expect(screen.queryByText('Velocity clamped')).not.toBeInTheDocument()
  })

  it('shows all after clicking all filter', () => {
    render(<EventLog entries={entries} />)
    fireEvent.click(screen.getByText('REJECT'))
    fireEvent.click(screen.getAllByText('all')[0])
    expect(screen.getByText('Velocity clamped')).toBeInTheDocument()
  })

  it('shows total entry count on the all filter pill', () => {
    render(<EventLog entries={entries} />)
    // The 'all' pill shows a count badge — 4 entries total
    const allButton = screen.getByRole('button', { name: /^all/i })
    expect(allButton).toBeInTheDocument()
    expect(allButton.textContent).toMatch('4')
  })
})
