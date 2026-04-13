import { render, screen } from '@testing-library/react'
import { RiskGauge } from '@/components/RiskGauge'

describe('RiskGauge', () => {
  it.each(['NORMAL', 'ELEVATED', 'CRITICAL', 'EMERGENCY'] as const)(
    'renders %s level', (level) => {
      render(<RiskGauge level={level} />)
      expect(screen.getByText(level)).toBeInTheDocument()
    }
  )

  it('shows "Risk Level" label', () => {
    render(<RiskGauge level="NORMAL" />)
    expect(screen.getByText('Risk Level')).toBeInTheDocument()
  })

  it('applies glow animation for EMERGENCY', () => {
    const { container } = render(<RiskGauge level="EMERGENCY" />)
    expect(container.firstChild).toHaveClass('animate-glow-red')
  })

  it('applies glow animation for NORMAL', () => {
    const { container } = render(<RiskGauge level="NORMAL" />)
    expect(container.firstChild).toHaveClass('animate-glow-green')
  })
})
