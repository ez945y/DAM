import { render, screen } from '@testing-library/react'
import { RiskBadge } from '@/components/RiskBadge'

describe('RiskBadge', () => {
  it('renders NORMAL', () => {
    render(<RiskBadge level="NORMAL" />)
    expect(screen.getByText('NORMAL')).toBeInTheDocument()
  })

  it('renders ELEVATED', () => {
    render(<RiskBadge level="ELEVATED" />)
    expect(screen.getByText('ELEVATED')).toBeInTheDocument()
  })

  it('renders CRITICAL', () => {
    render(<RiskBadge level="CRITICAL" />)
    expect(screen.getByText('CRITICAL')).toBeInTheDocument()
  })

  it('renders EMERGENCY', () => {
    render(<RiskBadge level="EMERGENCY" />)
    expect(screen.getByText('EMERGENCY')).toBeInTheDocument()
  })

  it('uses lg size class when size=lg', () => {
    const { container } = render(<RiskBadge level="NORMAL" size="lg" />)
    expect(container.firstChild).toHaveClass('text-base')
  })

  it('uses sm size class by default', () => {
    const { container } = render(<RiskBadge level="NORMAL" />)
    expect(container.firstChild).toHaveClass('text-xs')
  })
})
