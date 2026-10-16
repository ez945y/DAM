import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SafetyComparisonPanel } from '@/components/SafetyComparisonPanel'
import { api } from '@/lib/api'
import { safetyComparisonApi } from '@/lib/safety-comparison-api'

jest.mock('@/lib/api', () => ({ api: { listMcapSessions: jest.fn(), listStackfiles: jest.fn() } }))
jest.mock('@/lib/safety-comparison-api', () => ({ safetyComparisonApi: { compare: jest.fn(), cases: jest.fn(), save: jest.fn(), run: jest.fn() } }))

const outcome = { status: 'evaluated', decision: 'CLAMP', issues: [], correction_l2: 0.5, guards: [] }
const result = { id: 'comparison', status: 'complete', mcap: 'demo.mcap', total: 1, compared: 1, error_cycles: 0, difference_count: 1,
  distributions: { left: { PASS: 1 }, right: { CLAMP: 1 } }, configs: { left: { name: '__live__', sha256: 'abc' }, right: { name: 'precise', sha256: 'def' } },
  cycles: [{ cycle: 2, left: { ...outcome, decision: 'PASS' }, right: outcome, comparable: true, changed: true, output_delta_l2: 0.5 }], top_differences: [], limitations: ['Offline only'] }

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.listMcapSessions as jest.Mock).mockResolvedValue({ sessions: [{ filename: 'demo.mcap' }] })
  ;(api.listStackfiles as jest.Mock).mockResolvedValue({ live: { exists: true }, entries: [{ name: 'precise' }] })
  ;(safetyComparisonApi.cases as jest.Mock).mockResolvedValue({ cases: [], errors: [] })
  ;(safetyComparisonApi.compare as jest.Mock).mockResolvedValue(result)
})

test('compares two selected snapshots and saves a runnable expected-decision case', async () => {
  const saved = { id: 'case', name: 'Must clamp', cycle_start: 2, cycle_end: 2, config: { name: 'precise' } }
  ;(safetyComparisonApi.save as jest.Mock).mockResolvedValue(saved)
  ;(safetyComparisonApi.run as jest.Mock).mockResolvedValue({ id: 'case', status: 'fail', checked: 1, mismatches: [{ cycle: 2 }], issues: [] })
  render(<SafetyComparisonPanel />)
  const button = screen.getByRole('button', { name: 'Compare both configurations' })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  await screen.findByText(/1\/1 comparable cycles/)
  expect(safetyComparisonApi.compare).toHaveBeenCalledWith('demo.mcap', '__live__', 'precise')
  fireEvent.change(screen.getByLabelText('Regression name'), { target: { value: 'Must clamp' } })
  fireEvent.change(screen.getByLabelText('Expected result'), { target: { value: 'CLAMP' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  const rerun = await screen.findByRole('button', { name: 'Rerun case' })
  expect(safetyComparisonApi.save).toHaveBeenCalledWith(expect.objectContaining({ comparison_id: 'comparison', expected_decision: 'CLAMP', cycle_start: 2, cycle_end: 2 }))
  fireEvent.click(rerun)
  await screen.findByText(/FAIL · 1 assertions · 1 mismatches/)
})

test('surfaces unsupported input and never labels incomplete cycles as PASS', async () => {
  ;(safetyComparisonApi.compare as jest.Mock).mockResolvedValue({ ...result, status: 'error', compared: 0, error_cycles: 1, difference_count: 0,
    cycles: [{ cycle: 2, left: { status: 'unsupported', decision: null, issues: ['Sequence transitions are not recorded'] }, right: outcome, comparable: false, changed: false, output_delta_l2: null }] })
  render(<SafetyComparisonPanel />)
  const button = screen.getByRole('button', { name: 'Compare both configurations' })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  await screen.findByText('UNSUPPORTED')
  expect(screen.getByText(/0\/1 comparable cycles/)).toHaveTextContent('ERROR')
  expect(screen.getByText('Sequence transitions are not recorded')).toBeInTheDocument()
})

test('does not let an older refresh erase a newly saved regression', async () => {
  const { act } = await import('@testing-library/react')
  let finishRefresh!: (value: unknown) => void
  const pendingRefresh = new Promise(resolve => { finishRefresh = resolve })
  const saved = { id: 'new-case', name: 'Keep this case', cycle_start: 2, cycle_end: 2, config: { name: 'precise' } }
  ;(safetyComparisonApi.save as jest.Mock).mockResolvedValue(saved)
  render(<SafetyComparisonPanel />)
  const compare = screen.getByRole('button', { name: 'Compare both configurations' })
  await waitFor(() => expect(compare).toBeEnabled())
  ;(safetyComparisonApi.cases as jest.Mock).mockReturnValueOnce(pendingRefresh)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh choices' }))
  fireEvent.click(compare)
  await screen.findByText(/1\/1 comparable cycles/)
  fireEvent.change(screen.getByLabelText('Regression name'), { target: { value: saved.name } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  await screen.findByRole('button', { name: 'Rerun case' })
  await act(async () => { finishRefresh({ cases: [], errors: [] }) })
  expect(screen.getByRole('button', { name: 'Rerun case' })).toBeInTheDocument()
  expect(screen.getByText(/Keep this case · cycles/)).toBeInTheDocument()
})

test('disables saving ranges that include missing or unevaluated cycles', async () => {
  render(<SafetyComparisonPanel />)
  const compare = screen.getByRole('button', { name: 'Compare both configurations' })
  await waitFor(() => expect(compare).toBeEnabled())
  fireEvent.click(compare)
  await screen.findByText(/1\/1 comparable cycles/)
  fireEvent.change(screen.getByLabelText('Regression name'), { target: { value: 'Complete range' } })
  const save = screen.getByRole('button', { name: 'Save case' })
  expect(save).toBeEnabled()
  fireEvent.change(screen.getByLabelText('End cycle'), { target: { value: '3' } })
  expect(save).toBeDisabled()
  expect(screen.getByText(/Choose a complete range/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('End cycle'), { target: { value: '2' } })
  expect(save).toBeEnabled()
})

test('locks candidate selectors until a pending comparison finishes', async () => {
  const { act } = await import('@testing-library/react')
  let finish!: (value: unknown) => void
  ;(safetyComparisonApi.compare as jest.Mock).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<SafetyComparisonPanel />)
  const compare = screen.getByRole('button', { name: 'Compare both configurations' })
  await waitFor(() => expect(compare).toBeEnabled())
  fireEvent.click(compare)
  expect(screen.getByLabelText('Comparison recording')).toBeDisabled()
  expect(screen.getByLabelText('Left configuration')).toBeDisabled()
  expect(screen.getByLabelText('Right configuration')).toBeDisabled()
  await act(async () => { finish(result) })
  expect(screen.getByLabelText('Left configuration')).toBeEnabled()
})
