import { fireEvent, render, screen } from '@testing-library/react'
import { CycleSafetyInspector } from '@/components/CycleSafetyInspector'

describe('CycleSafetyInspector', () => {
  it('shows merged joint I/O and hardware columns in risk detail', () => {
    render(
      <CycleSafetyInspector
        mode="risk"
        displayUnit="rad"
        guards={[]}
        observation={{ joint_positions: [0.1], joint_velocities: [0.2] }}
        action={{ target_positions: [0.3], validated_positions: [0.25], was_clamped: true }}
        hardware={{ temperatures: { shoulder: 42 }, currents: { shoulder: 0.4 }, voltages: { shoulder: 7.4 } }}
      />,
    )

    fireEvent.click(screen.getByText('I/O'))

    expect(screen.getByText('Joint state and command')).toBeInTheDocument()
    expect(screen.getByText('State (rad)')).toBeInTheDocument()
    expect(screen.getByText('Target (rad)')).toBeInTheDocument()
    expect(screen.getByText('Output (rad)')).toBeInTheDocument()
    expect(screen.getByText('shoulder')).toBeInTheDocument()
    expect(screen.getByText('42.0')).toBeInTheDocument()
  })
})
