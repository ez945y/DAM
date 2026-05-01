import { render, screen, fireEvent } from '@testing-library/react'
import { GuardTable } from '@/components/GuardTable'
import type { GuardStatus } from '@/lib/types'

const guards: GuardStatus[] = [
  { name: 'MotionGuard', layer: 'L2', decision: 'PASS', reason: '' },
  { name: 'OODGuard', layer: 'L0', decision: 'REJECT', reason: 'OOD detected' },
  { name: 'ExecutionGuard', layer: 'L3', decision: 'CLAMP', reason: 'speed limit' },
]

describe('GuardTable', () => {
  it('renders empty state', () => {
    render(<GuardTable guards={[]} />)
    expect(screen.getByText(/no guards configured or active/i)).toBeInTheDocument()
  })

  it('renders layer headers for each active layer', () => {
    render(<GuardTable guards={guards} />)
    // Layers appear as badges in group headers
    expect(screen.getByText('L0')).toBeInTheDocument()
    expect(screen.getByText('L2')).toBeInTheDocument()
    expect(screen.getByText('L3')).toBeInTheDocument()
  })

  it('renders group decisions in layer headers', () => {
    render(<GuardTable guards={guards} />)
    expect(screen.getByText('REJECT')).toBeInTheDocument()
    expect(screen.getByText('PASS')).toBeInTheDocument()
    expect(screen.getByText('CLAMP')).toBeInTheDocument()
  })

  it('renders guard names when layer is expanded', () => {
    render(<GuardTable guards={guards} />)
    // Click the L2 header to expand it
    fireEvent.click(screen.getByText('TASK EXECUTION'))
    expect(screen.getByText('MotionGuard')).toBeInTheDocument()
  })

  it('renders layer titles', () => {
    render(<GuardTable guards={guards} />)
    expect(screen.getByText('PERCEPTION (OOD)')).toBeInTheDocument()
    expect(screen.getByText('TASK EXECUTION')).toBeInTheDocument()
    expect(screen.getByText('HARDWARE MONITORING')).toBeInTheDocument()
  })
})
