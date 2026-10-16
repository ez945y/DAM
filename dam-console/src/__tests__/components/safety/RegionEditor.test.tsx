import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { RegionEditor } from '@/components/safety/RegionEditor'
import { ComponentEditor } from '@/components/safety/ComponentEditor'
import type { SafetyNode, SafetyComponent } from '@/lib/safety'

const workspace: SafetyNode = { callback: 'workspace', params: { bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]] } }
function RegionHarness() {
  const [node, setNode] = useState(workspace)
  return <RegionEditor node={node} onChange={setNode} />
}

test('dragging an isometric box translates X/Y while preserving dimensions and Z', () => {
  render(<RegionHarness />)
  const svg = screen.getByRole('img', { name: 'Interactive 3D safety region' })
  svg.getBoundingClientRect = () => ({ width: 440, height: 320, x: 0, y: 0, top: 0, left: 0, bottom: 320, right: 440, toJSON: () => ({}) })
  Object.assign(svg, { setPointerCapture: jest.fn() })
  fireEvent(svg.querySelector('polygon')!, new MouseEvent('pointerdown', { bubbles: true, clientX: 100, clientY: 100 }))
  fireEvent(svg, new MouseEvent('pointermove', { bubbles: true, clientX: 128, clientY: 114 }))
  expect(screen.getByLabelText('X min (m)')).toHaveValue(-0.2)
  expect(screen.getByLabelText('X max (m)')).toHaveValue(0.6)
  expect(screen.getByLabelText('Y min (m)')).toHaveValue(-0.4)
  expect(screen.getByLabelText('Z min (m)')).toHaveValue(0.02)
  fireEvent.pointerUp(svg)
  fireEvent(svg, new MouseEvent('pointermove', { bubbles: true, clientX: 200, clientY: 200 }))
  expect(screen.getByLabelText('X min (m)')).toHaveValue(-0.2)
})

test('reviewed JSON updates geometry and geometry edits update JSON', () => {
  function Harness() {
    const [raw, setRaw] = useState<string | null>(null)
    const [component, setComponent] = useState<SafetyComponent>({ name: 'scene', kind: 'scene', boundaries: { region: { layer: 'L1', type: 'single', nodes: [workspace] } } })
    return <ComponentEditor component={component} onChange={setComponent} onRemove={jest.fn()} raw={raw} onRawChange={setRaw} frame="base_link" />
  }
  render(<Harness />)
  fireEvent.change(screen.getByLabelText('Boundary JSON scene'), { target: { value: JSON.stringify({ region: { layer: 'L1', type: 'single', nodes: [{ callback: 'workspace', params: { bounds: [[-1, 1], [-1, 1], [0, 1]] } }] } }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Use edited JSON' }))
  expect(screen.getByLabelText('X min (m)')).toHaveValue(-1)
  fireEvent.change(screen.getByLabelText('Z max (m)'), { target: { value: '0.5' } })
  const text = (screen.getByLabelText('Boundary JSON scene') as HTMLTextAreaElement).value
  expect(JSON.parse(text).region.nodes[0].params.bounds[2]).toEqual([0, 0.5])
})

test('invalid region shapes cannot crash the visual editor', () => {
  render(<RegionEditor node={{ callback: 'workspace', params: { bounds: [[1, 2]] } }} onChange={jest.fn()} />)
  expect(screen.getByText(/Region data is incomplete/)).toBeInTheDocument()
})


test('adding and deleting keep-out spheres preserves remaining coordinates and keeps the last sphere explicit', () => {
  function Harness() {
    const [node, setNode] = useState<SafetyNode>({ callback: 'keep_out_zone', params: { spheres: [[0.3, 0.1, 0.2, 0.15]] } })
    return <RegionEditor node={node} onChange={setNode} />
  }
  render(<Harness />)
  expect(screen.getByRole('button', { name: 'Remove sphere 1' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Add sphere' }))
  fireEvent.change(screen.getByLabelText('Sphere 2 X (m)'), { target: { value: '0.7' } })
  fireEvent.click(screen.getByRole('button', { name: 'Remove sphere 1' }))
  expect(screen.getByLabelText('Sphere 1 X (m)')).toHaveValue(0.7)
  expect(screen.queryByLabelText('Sphere 2 X (m)')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Remove sphere 1' })).toBeDisabled()
})
