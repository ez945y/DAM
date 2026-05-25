import { render, screen, fireEvent } from '@testing-library/react'
import { TemplateGallery } from '@/components/TemplateGallery'
import { TEMPLATES } from '@/lib/templates'

describe('TemplateGallery', () => {
  const onSelect = jest.fn()

  beforeEach(() => onSelect.mockClear())

  it('renders all templates', () => {
    render(<TemplateGallery templates={TEMPLATES} selected="quick_start" onSelect={onSelect} />)
    for (const t of TEMPLATES) {
      expect(screen.getAllByText(t.label).length).toBeGreaterThanOrEqual(1)
    }
  })

  it('marks selected template with checkmark', () => {
    render(<TemplateGallery templates={TEMPLATES} selected="dataset_replay_check" onSelect={onSelect} />)
    expect(screen.getByText('✓')).toBeInTheDocument()
  })

  it('calls onSelect when card clicked', () => {
    render(<TemplateGallery templates={TEMPLATES} selected="simulation" onSelect={onSelect} />)
    fireEvent.click(screen.getByText('SO-101 · ACT'))
    expect(onSelect).toHaveBeenCalledWith('so101')
  })

  it('renders badge for each template', () => {
    render(<TemplateGallery templates={TEMPLATES} selected="" onSelect={onSelect} />)
    expect(screen.getAllByText('LeRobot').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('ROS2').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Demo').length).toBeGreaterThanOrEqual(1)
  })

  it('aligns preset cards from the top when descriptions differ', () => {
    const { container } = render(<TemplateGallery templates={TEMPLATES} selected="" onSelect={onSelect} />)
    expect(container.firstChild).toHaveClass('items-start')
  })
})
