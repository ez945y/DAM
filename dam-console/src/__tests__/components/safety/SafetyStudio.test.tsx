import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SafetyStudio } from '@/components/safety/SafetyStudio'
import { safetyApi } from '@/lib/safety'
import { api } from '@/lib/api'
import { SAFETY_DRAFT_KEY } from '@/components/safety/useSafetyDraft'

jest.mock('@/lib/api', () => ({ api: { listStackfiles: jest.fn(), getStackfile: jest.fn(), createStackfile: jest.fn() } }))
jest.mock('@/lib/safety', () => ({
  ...jest.requireActual('@/lib/safety'),
  safetyApi: { list: jest.fn(), load: jest.fn(), save: jest.fn(), compile: jest.fn(), draft: jest.fn(), preview: jest.fn(), apply: jest.fn(), status: jest.fn(), rollbackPreview: jest.fn() },
}))
const mocked = safetyApi as jest.Mocked<typeof safetyApi>
const compiled = { yaml: 'version: "1"', task: 'default', valid: true, sources: { speed: { component: 'mode', kind: 'mode' as const } }, diagnostics: [], runtime_verified: false as const }
const preview = { token: 'reviewed-token', mode: 'hot_reload' as const, reasons: ['Parameter-only change'], changes: [{ path: 'max_velocities', before: 1, after: 0.5 }], current_version: 4, candidate_hash: 'candidate' }

beforeEach(() => {
  jest.restoreAllMocks()
  jest.clearAllMocks()
  localStorage.clear()
  mocked.list.mockResolvedValue({ plans: [] })
  ;(api.listStackfiles as jest.Mock).mockResolvedValue({ entries: [], live: { exists: false } })
  mocked.status.mockResolvedValue({ active_hash: 'old', config_version: 4, pending: null, history: [] })
  mocked.compile.mockResolvedValue(compiled)
  mocked.preview.mockResolvedValue(preview)
  mocked.apply.mockResolvedValue({ id: 'switch-1', status: 'saved_restart_required', message: 'Saved only. Restart required.' })
})
async function setup() {
  render(<SafetyStudio />)
  await waitFor(() => expect(mocked.list).toHaveBeenCalled())
  fireEvent.change(screen.getByLabelText('Base Stackfile YAML'), { target: { value: 'version: "1"' } })
}
async function previewPlan() {
  fireEvent.click(screen.getByRole('button', { name: 'Compile & check' }))
  await screen.findByRole('button', { name: 'Preview switch' })
  fireEvent.click(screen.getByRole('button', { name: 'Preview switch' }))
  await screen.findByRole('button', { name: 'Apply reviewed switch' })
}

test('editing a checked plan invalidates the preview and cannot apply stale token', async () => {
  await setup(); await previewPlan()
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'careful' } })
  expect(screen.queryByRole('button', { name: 'Apply reviewed switch' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Preview switch' })).not.toBeInTheDocument()
  expect(mocked.apply).not.toHaveBeenCalled()
})

test('blocked compile cannot preview and diagnostics expose missing inputs', async () => {
  mocked.compile.mockResolvedValue({ ...compiled, valid: false, diagnostics: [{ boundary: 'speed', status: 'missing', message: 'Joint observations are missing', code: 'missing_obs' }] })
  await setup()
  fireEvent.click(screen.getByRole('button', { name: 'Compile & check' }))
  expect(await screen.findByText(/Joint observations are missing/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Preview switch' })).toBeDisabled()
})

test('submits only the reviewed token and distinguishes saved from applied', async () => {
  await setup(); await previewPlan()
  fireEvent.click(screen.getByRole('button', { name: 'Apply reviewed switch' }))
  await screen.findByText('Saved only. Restart required.')
  expect(mocked.apply).toHaveBeenCalledWith('reviewed-token')
  expect(screen.getByText('Saved · restart required')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Apply reviewed switch' })).not.toBeInTheDocument()
})

test('natural language draft is not silently added and preserves unresolved requirements after review', async () => {
  mocked.draft.mockResolvedValue({ components: [{ name: 'gentle', kind: 'mode', boundaries: {} }], unresolved: ['Define how close to the table'], explanation: 'Review values.', runtime_verified: false })
  await setup()
  fireEvent.change(screen.getByLabelText('Rule description'), { target: { value: 'Slow down near the table' } })
  fireEvent.click(screen.getByRole('button', { name: 'Generate draft' }))
  await screen.findByText('Define how close to the table')
  expect(screen.queryByLabelText('Component name')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Add draft components for review' }))
  expect(screen.getByLabelText('Component name')).toHaveValue('gentle')
  expect(screen.getByText('Define how close to the table')).toBeInTheDocument()
  expect(mocked.apply).not.toHaveBeenCalled()
})

test('pending invalid advanced JSON invalidates preview and disables checks', async () => {
  await setup()
  fireEvent.click(screen.getByRole('button', { name: '+ mode' }))
  await previewPlan()
  fireEvent.change(screen.getByLabelText('Boundary JSON mode_1'), { target: { value: '{bad' } })
  expect(screen.queryByRole('button', { name: 'Apply reviewed switch' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Compile & check' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Use edited JSON' }))
  expect(screen.getByRole('alert')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Discard JSON edits' }))
  expect(screen.getByRole('button', { name: 'Compile & check' })).toBeEnabled()
})

test('coordinate regions require an explicit native frame declaration and confirmation', async () => {
  await setup()
  fireEvent.click(screen.getByRole('button', { name: '+ scene' }))
  fireEvent.change(screen.getByLabelText('New rule'), { target: { value: 'workspace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
  expect(screen.getByRole('button', { name: 'Compile & check' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Native solver frame (required for regions)'), { target: { value: 'base_link' } })
  fireEvent.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: 'Compile & check' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('X min (m)'), { target: { value: '-0.2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Compile & check' }))
  await waitFor(() => expect(mocked.compile).toHaveBeenCalledWith(expect.objectContaining({ contract: expect.objectContaining({ frame: 'base_link' }), components: [expect.objectContaining({ boundaries: expect.objectContaining({ scene_1_workspace: expect.objectContaining({ nodes: [expect.objectContaining({ params: { bounds: [[-0.2, 0.4], [-0.4, 0.4], [0.02, 0.6]] } })] }) }) })] })))
})


test('exports the compiled candidate through exclusive Stackfile creation for replay comparison', async () => {
  ;(api.createStackfile as jest.Mock).mockResolvedValue({ name: 'candidate.yaml', created: true })
  await setup()
  fireEvent.click(screen.getByRole('button', { name: 'Compile & check' }))
  await screen.findByRole('button', { name: 'Save compiled config for comparison' })
  fireEvent.change(screen.getByLabelText('Comparison Stackfile name'), { target: { value: 'candidate' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save compiled config for comparison' }))
  await waitFor(() => expect(api.createStackfile).toHaveBeenCalledWith('candidate', compiled.yaml))
  expect(await screen.findByText(/Saved candidate.yaml in the Stackfile library/)).toBeInTheDocument()
  expect(mocked.apply).not.toHaveBeenCalled()
})


test('refresh restores every authoring input and invalid boundary JSON without reviving a preview token', async () => {
  const first = render(<SafetyStudio />)
  await waitFor(() => expect(mocked.list).toHaveBeenCalled())
  fireEvent.change(screen.getByLabelText('Plan name'), { target: { value: 'unfinished-plan' } })
  fireEvent.change(screen.getByLabelText('Base Stackfile YAML'), { target: { value: 'version: "1"' } })
  fireEvent.change(screen.getByLabelText('Available observation groups (comma separated)'), { target: { value: 'joints, temperature, ' } })
  fireEvent.change(screen.getByLabelText('Rule description'), { target: { value: 'Hold near the table' } })
  fireEvent.click(screen.getByRole('button', { name: '+ mode' }))
  await previewPlan()
  fireEvent.change(screen.getByLabelText('Comparison Stackfile name'), { target: { value: 'compare-later' } })
  const saved = JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!)
  expect(saved).not.toHaveProperty('preview')
  expect(JSON.stringify(saved)).not.toContain('reviewed-token')
  fireEvent.change(screen.getByLabelText('Boundary JSON mode_1'), { target: { value: '{"unfinished":' } })
  // No timers/effect flush is needed: the last edit is written synchronously.
  const latest = JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!)
  expect(latest.boundaryEdits['0']).toBe('{"unfinished":')
  localStorage.setItem(SAFETY_DRAFT_KEY, JSON.stringify({ ...latest, preview, compiled }))
  first.unmount()
  render(<SafetyStudio />)
  expect(await screen.findByText(/Restored local draft, including pending edits/)).toBeInTheDocument()
  expect(screen.getByLabelText('Plan name')).toHaveValue('unfinished-plan')
  expect(screen.getByLabelText('Boundary JSON mode_1')).toHaveValue('{"unfinished":')
  expect(screen.getByLabelText('Rule description')).toHaveValue('Hold near the table')
  expect(screen.getByLabelText('Available observation groups (comma separated)')).toHaveValue('joints, temperature, ')
  expect(screen.getByRole('button', { name: 'Compile & check' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Apply reviewed switch' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Preview switch' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Discard JSON edits' }))
  fireEvent.click(screen.getByRole('button', { name: 'Compile & check' }))
  expect(await screen.findByLabelText('Comparison Stackfile name')).toHaveValue('compare-later')
  expect(mocked.apply).not.toHaveBeenCalled()
})

test('removing a component preserves and reindexes other components pending edits across refresh', async () => {
  const first = render(<SafetyStudio />)
  fireEvent.click(screen.getByRole('button', { name: '+ baseline' }))
  fireEvent.click(screen.getByRole('button', { name: '+ mode' }))
  fireEvent.change(screen.getByLabelText('Boundary JSON mode_2'), { target: { value: '{broken but retained' } })
  fireEvent.click(screen.getAllByRole('button', { name: 'Remove component' })[0])
  expect(screen.getByLabelText('Boundary JSON mode_2')).toHaveValue('{broken but retained')
  first.unmount()
  render(<SafetyStudio />)
  expect(await screen.findByLabelText('Boundary JSON mode_2')).toHaveValue('{broken but retained')
  expect(JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!).boundaryEdits).toEqual({ '0': '{broken but retained' })
})

test('storage failures preserve in-memory edits and retry saves the latest version', async () => {
  await setup()
  const store = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Quota exceeded', 'QuotaExceededError') })
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'still-editable' } })
  expect(screen.getByRole('alert')).toHaveTextContent('Local draft saving is unavailable')
  expect(screen.getByLabelText('Task')).toHaveValue('still-editable')
  store.mockRestore()
  fireEvent.click(screen.getByRole('button', { name: 'Retry local storage' }))
  expect(JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!).plan.task).toBe('still-editable')
  expect(screen.queryByText(/Local draft saving is unavailable/)).not.toBeInTheDocument()
})

test('damaged stored draft is not silently overwritten on mount or retry', async () => {
  localStorage.setItem(SAFETY_DRAFT_KEY, '{damaged')
  render(<SafetyStudio />)
  expect(await screen.findByText(/Local draft could not be restored/)).toBeInTheDocument()
  expect(localStorage.getItem(SAFETY_DRAFT_KEY)).toBe('{damaged')
  fireEvent.click(screen.getByRole('button', { name: 'Retry local storage' }))
  expect(localStorage.getItem(SAFETY_DRAFT_KEY)).toBe('{damaged')
})

test('a late plan load cannot overwrite edits made after the request started', async () => {
  let resolveLoad!: (value: any) => void
  mocked.list.mockResolvedValue({ plans: [{ name: 'remote-plan' }] })
  mocked.load.mockImplementation(() => new Promise(resolve => { resolveLoad = resolve }))
  await setup()
  await waitFor(() => expect(within(screen.getByLabelText('Saved plans')).getByRole('option', { name: 'remote-plan' })).toBeInTheDocument())
  fireEvent.change(screen.getByLabelText('Saved plans'), { target: { value: 'remote-plan' } })
  // Model a pending browser input delivered after the request starts.
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'new-local-task' } })
  await act(async () => resolveLoad({ name: 'remote-plan', task: 'old-task', base_yaml: 'remote', components: [] }))
  expect(screen.getByLabelText('Task')).toHaveValue('new-local-task')
  expect(screen.getByLabelText('Base Stackfile YAML')).toHaveValue('version: "1"')
  expect(JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!).plan.task).toBe('new-local-task')
})

test('late generated rules from an unmounted editor cannot overwrite a restored draft', async () => {
  let resolveDraft!: (value: any) => void
  mocked.draft.mockImplementation(() => new Promise(resolve => { resolveDraft = resolve }))
  const first = render(<SafetyStudio />)
  fireEvent.change(screen.getByLabelText('Rule description'), { target: { value: 'first draft request' } })
  fireEvent.click(screen.getByRole('button', { name: 'Generate draft' }))
  first.unmount()
  render(<SafetyStudio />)
  fireEvent.change(screen.getByLabelText('Rule description'), { target: { value: 'newer draft request' } })
  await act(async () => resolveDraft({ components: [], unresolved: ['stale unresolved'], explanation: 'old request', runtime_verified: false }))
  expect(screen.getByLabelText('Rule description')).toHaveValue('newer draft request')
  expect(screen.queryByText('stale unresolved')).not.toBeInTheDocument()
  expect(JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!).description).toBe('newer draft request')
  expect(JSON.parse(localStorage.getItem(SAFETY_DRAFT_KEY)!).generatedDraft).toBeNull()
})

test('reports draft saving separately from plan library saving and active read errors', async () => {
  mocked.status.mockResolvedValue({ active_hash: null, config_version: null, pending: null, history: [], active_error: 'Active YAML invalid', history_error: 'History unreadable' })
  mocked.save.mockResolvedValue({ name: 'my-safety-plan', created: true })
  await setup()
  fireEvent.click(screen.getByRole('button', { name: 'Save plan' }))
  await screen.findByText('Plan saved. The active runtime has not changed.')
  expect(within(screen.getByRole('region', { name: 'Draft status' })).getByText(/Plan library: saved/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'modified-task' } })
  expect(screen.getByText(/Plan library: local changes/)).toBeInTheDocument()
  expect(screen.getByText(/Active configuration error: Active YAML invalid/)).toBeInTheDocument()
  expect(screen.getByText(/Switch history error: History unreadable/)).toBeInTheDocument()
})
