'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { safetyApi, safetyButton, safetyInput, type SafetyPlan, type CompileResult, type SwitchPreview, type SwitchStatus, type SwitchRecord, type ComponentKind, type SafetyComponent } from '@/lib/safety'
import { ComponentEditor } from './ComponentEditor'
import { useSafetyDraft, emptySafetyPlan, declarationText, type DeclarationText } from './useSafetyDraft'

const split = (text: string) => text.split(',').map(value => value.trim()).filter(Boolean)
const describe = (value: unknown) => value === undefined ? '—' : JSON.stringify(value)
const labels: Record<SwitchRecord['status'], string> = { pending: 'Waiting to apply', applied: 'Applied', saved_restart_required: 'Saved · restart required', rejected: 'Rejected' }

export function SafetyStudio() {
  const local = useSafetyDraft()
  const { plan, boundaryEdits, description: draftText, generatedDraft: draft, exportName, declarations } = local.draft
  const [savedPlanFingerprint, setSavedPlanFingerprint] = useState<string | null>(null)
  const [plans, setPlans] = useState<{ name: string }[]>([])
  const [library, setLibrary] = useState<{ name: string }[]>([])
  const [reusable, setReusable] = useState<SafetyComponent[]>([])
  const [compiled, setCompiled] = useState<CompileResult | null>(null)
  const [preview, setPreview] = useState<SwitchPreview | null>(null)
  const [status, setStatus] = useState<SwitchStatus | null>(null)
  const [result, setResult] = useState<SwitchRecord | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [generation, setGeneration] = useState(0)
  const [frameConfirmed, setFrameConfirmed] = useState(false)
  const revision = useRef(0)
  const operation = useRef(0)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; revision.current++; operation.current++ } }, [])
  const invalidate = () => { revision.current++; setCompiled(null); setPreview(null); setResult(null); setNotice('') }
  const update = (next: SafetyPlan) => { invalidate(); local.edit({ plan: next }) }
  const replace = (next: SafetyPlan) => {
    invalidate()
    local.edit({ plan: next, boundaryEdits: {}, declarations: declarationText(next) })
    setGeneration(value => value + 1)
    setFrameConfirmed(false)
  }
  const removeComponent = (index: number) => {
    invalidate()
    local.edit(previous => ({
      plan: { ...previous.plan, components: previous.plan.components.filter((_, i) => i !== index) },
      boundaryEdits: Object.fromEntries(Object.entries(previous.boundaryEdits).filter(([key]) => Number(key) !== index).map(([key, text]) => [Number(key) > index ? Number(key) - 1 : Number(key), text])),
    }))
    setGeneration(value => value + 1)
    setFrameConfirmed(false)
  }
  const editBoundaryJson = (index: number, text: string | null) => {
    invalidate()
    local.edit(previous => {
      const edits = { ...previous.boundaryEdits }
      if (text === null) delete edits[index]
      else edits[index] = text
      return { boundaryEdits: edits }
    })
  }
  const working = Boolean(busy)
  const hasPending = Object.keys(boundaryEdits).length > 0
  const hasRegions = plan.components.some(component => Object.values(component.boundaries).some(boundary => boundary.nodes.some(node => node.callback === 'workspace' || node.callback === 'keep_out_zone')))
  const coordinateReady = !hasRegions || (Boolean(plan.contract?.frame?.trim()) && frameConfirmed)
  const run = async (label: string, work: (isCurrent: () => boolean) => Promise<void>) => {
    const id = ++operation.current
    const version = revision.current
    const isCurrent = () => mounted.current && operation.current === id && revision.current === version
    setBusy(label); setError(''); setNotice('')
    try { await work(isCurrent) } catch (caught) {
      if (isCurrent()) setError(caught instanceof Error ? caught.message : String(caught))
    } finally { if (mounted.current && operation.current === id) setBusy('') }
  }
  useEffect(() => {
    let active = true
    Promise.allSettled([safetyApi.list(), api.listStackfiles(), safetyApi.status()]).then(results => {
      if (!active) return
      if (results[0].status === 'fulfilled') setPlans(results[0].value.plans)
      if (results[1].status === 'fulfilled') setLibrary(results[1].value.entries)
      if (results[2].status === 'fulfilled') setStatus(results[2].value)
      const failures = results.filter(r => r.status === 'rejected') as PromiseRejectedResult[]
      if (failures.length) setError(failures.map(r => String(r.reason)).join('; '))
    })
    const interval = setInterval(() => { safetyApi.status().then(value => { if (active) setStatus(value) }).catch(() => { if (active) setStatus(null) }) }, 3000)
    return () => { active = false; clearInterval(interval) }
  }, [])
  const updateContract = (patch: Partial<NonNullable<SafetyPlan['contract']>>) => update({ ...plan, contract: { observation_keys: [], ...plan.contract, ...patch } })
  const editDeclaration = (key: keyof DeclarationText, text: string) => {
    invalidate()
    local.edit(previous => ({
      declarations: { ...previous.declarations, [key]: text },
      plan: { ...previous.plan, contract: { observation_keys: [], ...previous.plan.contract, [key]: split(text) } },
    }))
  }
  const librarySaved = savedPlanFingerprint === JSON.stringify(plan) && !hasPending
  return <main className="p-6 space-y-6 max-w-[1600px] mx-auto w-full">
    <header className="flex flex-wrap justify-between gap-4 items-center">
      <div><h1 className="text-2xl font-bold">Safety Studio</h1><p className="text-xs text-dam-muted mt-2">Compose protection, check requirements, compare changes, then apply a reviewed version.</p></div>
      <Link className={safetyButton} href="/replay">Compare recorded actions →</Link>
    </header>
    <section aria-label="Draft status" className="rounded-lg border border-dam-border p-3 space-y-2 text-xs">
      <p>{!local.ready ? 'Restoring local draft…' : local.savedAt && !local.storageError ? `Local draft saved · ${new Date(local.savedAt).toLocaleString()}` : 'Local draft has not been saved.'}</p>
      {local.restored && <p className="text-dam-blue">Restored local draft, including pending edits. Compile and preview again before applying.</p>}
      <p className="text-dam-muted">Plan library: {librarySaved ? 'saved' : 'local changes or not saved in this session'}. Local drafts do not change the active runtime.</p>
      {local.storageError && <div role="alert" className="text-dam-orange"><p>{local.storageError}</p><button className={`${safetyButton} mt-2`} type="button" onClick={local.retrySave}>Retry local storage</button></div>}
    </section>
    <section className="rounded-lg border border-dam-border p-4 bg-dam-surface space-y-2" aria-label="Active configuration">
      <div className="flex flex-wrap gap-5 text-xs"><span>Active version: <strong>{status?.config_version ?? 'unknown'}</strong></span><span>Active task: {status?.active_task ?? 'unavailable'}</span><span className="break-all">Active hash: {status?.active_hash ?? 'unavailable'}</span><span className="break-all">Saved hash: {status?.saved_hash ?? 'unavailable'}</span></div>
      {status?.active_error && <p className="text-dam-orange text-xs">Active configuration error: {status.active_error}</p>}
      {status?.history_error && <p className="text-dam-orange text-xs">Switch history error: {status.history_error}</p>}
      {status?.saved_error && <p className="text-dam-orange text-xs">Saved configuration error: {status.saved_error}</p>}
      {status?.pending && <p className="text-dam-orange text-xs">{labels[status.pending.status]}: {status.pending.message}</p>}
      {!status && <p className="text-dam-orange text-xs">Runtime status unavailable. Saved plans and previews do not confirm active protection.</p>}
      <details><summary className="text-xs text-dam-muted cursor-pointer">Switch history ({status?.history.length ?? 0})</summary><ul className="space-y-2 mt-2">{status?.history.map(item => <li key={item.id} className="text-xs"><strong>{labels[item.status]}</strong> · {item.message}
        {item.persisted === false && item.status === 'applied' && <span className="text-dam-orange"> · Active but not saved</span>}
        {['applied', 'saved_restart_required'].includes(item.status) && <button className={`${safetyButton} ml-2`} type="button" disabled={working || hasPending} onClick={() => void run('Previewing rollback', async isCurrent => { setPreview(null); setResult(null); const response = await safetyApi.rollbackPreview(item.id, plan.contract); if (isCurrent()) setPreview(response) })}>Preview rollback</button>}</li>)}</ul></details>
    </section>
    {error && <div role="alert" className="rounded border border-dam-red/40 bg-dam-red/10 p-3 text-sm text-dam-red">{error}</div>}
    {notice && <p role="status" className="text-dam-green text-sm">{notice}</p>}
    {working && <p role="status" className="text-dam-blue text-sm">{busy}…</p>}
    <fieldset disabled={working || !local.ready} className={`space-y-6 disabled:opacity-70 ${working ? 'pointer-events-none' : ''}`}>
      <section className="rounded-lg border border-dam-border p-4 space-y-4">
        <h2 className="font-semibold text-sm">1 · Choose a base and compose a plan</h2>
        <div className="grid md:grid-cols-3 gap-3">
          <label className="text-xs text-dam-muted">Plan name<input className={safetyInput} value={plan.name} onChange={event => update({ ...plan, name: event.target.value })} /></label>
          <label className="text-xs text-dam-muted">Task<input className={safetyInput} value={plan.task} onChange={event => update({ ...plan, task: event.target.value })} /></label>
          <label className="text-xs text-dam-muted">Saved plans<select className={safetyInput} value="" onChange={event => { const name = event.target.value; if (name) void run('Loading plan', async isCurrent => { const loaded = await safetyApi.load(name); if (isCurrent()) { replace(loaded); setSavedPlanFingerprint(JSON.stringify(loaded)) } }) }}><option value="">Load a saved plan…</option>{plans.map(item => <option key={item.name}>{item.name}</option>)}</select></label>
        </div>
        <div className="flex flex-wrap gap-2">
          <select aria-label="Base Stackfile" className={`${safetyInput} max-w-xs`} value="" onChange={event => { const name = event.target.value; if (name) void run('Loading base Stackfile', async isCurrent => { const yaml = await api.getStackfile(name); if (isCurrent()) update({ ...plan, base_yaml: yaml }) }) }}><option value="">Import a library Stackfile…</option>{library.map(item => <option key={item.name}>{item.name}</option>)}</select>
          <button className={safetyButton} type="button" onClick={() => {
            try {
              const yaml = localStorage.getItem('dam_yaml_v1')
              if (yaml) update({ ...plan, base_yaml: yaml }); else setError('No locally saved Config YAML. Import a library Stackfile or paste one below.')
            } catch { setError('Local storage is unavailable. Import a library Stackfile or paste one below.') }
          }}>Import Config draft</button>
          <button className={safetyButton} type="button" onClick={() => replace(emptySafetyPlan())}>New plan</button>
          <button className={safetyButton} type="button" disabled={!plan.name.trim() || hasPending} onClick={() => void run('Saving plan', async isCurrent => {
            await safetyApi.save(plan)
            if (!isCurrent()) return
            setSavedPlanFingerprint(JSON.stringify(plan)); setNotice('Plan saved. The active runtime has not changed.')
            const listed = await safetyApi.list(); if (isCurrent()) setPlans(listed.plans)
          })}>Save plan</button>
        </div>
        <label className="block text-xs text-dam-muted">Base Stackfile YAML<textarea className={`${safetyInput} mt-1 font-mono text-xs`} rows={8} placeholder="Paste a Stackfile with hardware, guards, tasks and existing baseline rules." value={plan.base_yaml} onChange={event => update({ ...plan, base_yaml: event.target.value })} /></label>
        <details><summary className="text-xs text-dam-muted cursor-pointer">Input and coordinate declarations</summary><div className="grid md:grid-cols-2 gap-3 mt-3">
          <label className="text-xs text-dam-muted">Available observation groups (comma separated)<input className={safetyInput} value={declarations.observation_keys} onChange={event => editDeclaration('observation_keys', event.target.value)} /></label>
          <label className="text-xs text-dam-muted">Native solver frame (required for regions)<input className={safetyInput} placeholder="e.g. base_link — match your FK solver" value={plan.contract?.frame ?? ''} onChange={event => { setFrameConfirmed(false); updateContract({ frame: event.target.value || undefined }) }} /></label>
          <label className="text-xs text-dam-muted">Action units (comma separated)<input className={safetyInput} value={declarations.action_units} onChange={event => editDeclaration('action_units', event.target.value)} /></label>
          <label className="text-xs text-dam-muted">Joint names in command order (comma separated)<input className={safetyInput} value={declarations.joint_names} onChange={event => editDeclaration('joint_names', event.target.value)} /></label>
        </div><p className="text-xs text-dam-muted mt-2">These are declarations, not live measurements. Region coordinates must already use the solver’s native frame. Dynamic frames and coordinate conversion are not supported.</p></details>
        {hasRegions && <label className="flex items-start gap-2 text-xs text-dam-muted"><input type="checkbox" checked={frameConfirmed} disabled={!plan.contract?.frame?.trim()} onChange={event => { invalidate(); setFrameConfirmed(event.target.checked) }} />I confirm all region coordinates already use the declared solver frame ({plan.contract?.frame || 'set the frame above'}). No coordinate conversion is performed.</label>}
        <div className="flex flex-wrap gap-2">{(['baseline', 'scene', 'task', 'mode'] as ComponentKind[]).map(kind => <button key={kind} type="button" className={safetyButton} onClick={() => {
          let name = `${kind}_${plan.components.length + 1}`
          while (plan.components.some(component => component.name === name)) name += '_new'
          update({ ...plan, components: [...plan.components, { name, kind, boundaries: {} }] })
        }}>+ {kind}</button>)}</div>
        <p className="text-xs text-dam-muted">Each component adds rules; conflicting names fail validation. Baseline protection remains always active. Spatial speed zones are not supported; the speed form creates a global end-effector limit.</p>
        <div className="space-y-2"><select aria-label="Reusable components" className={`${safetyInput} max-w-sm`} value="" onChange={event => { const name = event.target.value; if (name) void run('Loading reusable components', async isCurrent => { const loaded = await safetyApi.load(name); if (isCurrent()) setReusable(loaded.components) }) }}><option value="">Reuse components from a saved plan…</option>{plans.map(item => <option key={item.name}>{item.name}</option>)}</select>
          <div className="flex gap-2 flex-wrap">{reusable.map((component, index) => <button key={index} className={safetyButton} type="button" onClick={() => { setFrameConfirmed(false); update({ ...plan, components: [...plan.components, component] }) }}>Add {component.name} ({component.kind})</button>)}</div>
        </div>
        <div className="grid xl:grid-cols-2 gap-4">{plan.components.map((component, index) => <ComponentEditor key={`${generation}-${index}`} component={component} frame={plan.contract?.frame} onChange={next => update({ ...plan, components: plan.components.map((item, i) => i === index ? next : item) })} raw={boundaryEdits[index] ?? null} onRawChange={text => editBoundaryJson(index, text)} onRemove={() => removeComponent(index)} />)}</div>
      </section>
      <section className="rounded-lg border border-dam-border p-4 space-y-3">
        <h2 className="font-semibold text-sm">Draft rules from a description</h2>
        <p className="text-xs text-dam-muted">Use explicit values and units. Unsupported or ambiguous requests are listed for review.</p>
        <textarea aria-label="Rule description" className={safetyInput} rows={3} value={draftText} placeholder="Joint velocity limit 0.5 rad/s" onChange={event => { invalidate(); local.edit({ description: event.target.value, generatedDraft: null }) }} />
        <button className={safetyButton} disabled={!draftText.trim()} type="button" onClick={() => void run('Drafting rules', async isCurrent => { const generated = await safetyApi.draft(draftText, plan.contract?.frame); if (isCurrent()) local.edit({ generatedDraft: generated }) })}>Generate draft</button>
        {draft && <div className="space-y-3">
          <p className="text-xs text-dam-muted">{draft.explanation}</p>
          {draft.unresolved.length > 0 && <div className="border border-dam-orange/40 p-3 rounded"><p className="text-xs font-semibold text-dam-orange">Unresolved — these requests are not implemented by the draft</p><ul className="list-disc pl-5 text-xs mt-2">{draft.unresolved.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
          <pre className="overflow-auto max-h-64 text-xs bg-dam-bg p-3 rounded">{JSON.stringify(draft.components, null, 2)}</pre>
          <button className={safetyButton} disabled={!draft.components.length || hasPending} type="button" onClick={() => { setFrameConfirmed(false); update({ ...plan, components: [...plan.components, ...draft.components] }); local.edit({ generatedDraft: { ...draft, components: [] } }); setNotice('Draft components added for review. Edit their values and run requirement checks before applying.') }}>Add draft components for review</button>
        </div>}
      </section>
      <section className="rounded-lg border border-dam-border p-4 space-y-4">
        <div className="flex flex-wrap gap-3 justify-between items-center"><h2 className="font-semibold text-sm">2 · Check requirements and rule sources</h2><button type="button" className={`${safetyButton} text-dam-blue`} disabled={hasPending || !coordinateReady || !plan.base_yaml.trim() || !plan.task.trim()} onClick={() => void run('Checking requirements', async isCurrent => {
          setCompiled(null); setPreview(null); setResult(null); const response = await safetyApi.compile(plan); if (isCurrent()) setCompiled(response)
        })}>Compile & check</button></div>
        {hasPending && <p className="text-dam-orange text-xs">Apply or discard the pending JSON edits before saving, checking or switching.</p>}
        {compiled && <>
          <p className={`text-xs ${compiled.valid ? 'text-dam-blue' : 'text-dam-orange'}`}>{compiled.valid ? 'Static requirements satisfied. Runtime inputs and enforcement are not verified.' : 'Requirements need attention. Resolve missing inputs and errors before switching.'}</p>
          <div className="overflow-x-auto"><table className="w-full text-xs text-left"><thead><tr className="text-dam-muted"><th className="p-2">Rule</th><th>Source</th><th>Status / requirement</th></tr></thead><tbody>{Object.entries(compiled.sources).map(([name, source]) => <tr key={name} className="border-t border-dam-border"><td className="p-2 align-top">{name}</td><td className="align-top py-2">{source.component} · {source.kind}</td><td className="py-2">{compiled.diagnostics.filter(item => item.boundary === name).map((item, index) => <p key={index} className={item.status === 'error' || item.status === 'missing' ? 'text-dam-orange' : 'text-dam-muted'}>{item.status}: {item.message}</p>)}</td></tr>)}</tbody></table></div>
          {compiled.diagnostics.filter(item => !compiled.sources[item.boundary]).map((item, index) => <p className="text-xs text-dam-orange" key={index}>{item.boundary}: {item.message}</p>)}
          <details><summary className="text-xs cursor-pointer text-dam-muted">Compiled Stackfile</summary><textarea aria-label="Compiled Stackfile" className={`${safetyInput} font-mono text-xs mt-2`} rows={10} readOnly value={compiled.yaml} /></details>
          <div className="flex flex-wrap gap-2 items-center">
            <input aria-label="Comparison Stackfile name" className={`${safetyInput} max-w-xs`} value={exportName} placeholder="Optional comparison filename" onChange={event => local.edit({ exportName: event.target.value })} />
            <button className={safetyButton} type="button" disabled={!compiled.valid || hasPending} onClick={() => void run('Saving comparison Stackfile', async isCurrent => {
              const name = exportName.trim() || `${plan.name}-${Date.now()}`
              const saved = await api.createStackfile(name, compiled.yaml)
              const listed = await api.listStackfiles()
              if (!isCurrent()) return
              setLibrary(listed.entries)
              setNotice(`Saved ${saved.name} in the Stackfile library. Select it on Comparison with task ${compiled.task}. Existing files are never overwritten.`)
            })}>Save compiled config for comparison</button>
            <Link href="/replay" className={safetyButton}>Open Comparison →</Link>
          </div>
          <button className={safetyButton} type="button" disabled={!compiled.valid || hasPending} onClick={() => void run('Previewing switch', async isCurrent => {
            setPreview(null); const response = await safetyApi.preview(compiled.yaml, compiled.task, plan.contract); if (isCurrent()) setPreview(response)
          })}>Preview switch</button>
        </>}
      </section>
      {preview && <section className="rounded-lg border border-dam-blue/40 p-4 space-y-3">
        <h2 className="font-semibold text-sm">3 · Review switch · {preview.mode.replaceAll('_', ' ')}</h2>
        <p className="text-xs text-dam-muted break-all">Reviewed runtime version: {preview.current_version ?? 'unavailable'} · Candidate: {preview.candidate_hash}</p>
        <ul className="text-xs list-disc pl-5 space-y-1">{preview.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>
        <div className="overflow-auto max-h-80"><table className="w-full text-left text-xs"><thead><tr><th className="p-2">Setting</th><th>Current</th><th>Candidate</th></tr></thead><tbody>{preview.changes.map((change, index) => <tr key={index} className="border-t border-dam-border"><td className="p-2">{change.path}</td><td className="p-2 break-all">{describe(change.before)}</td><td className="p-2 break-all">{describe(change.after)}</td></tr>)}</tbody></table></div>
        <p className="text-xs text-dam-muted">Editing the plan invalidates this preview. Saving a structural change requires the existing restart workflow; this page does not start the robot.</p>
        <button className={`${safetyButton} text-dam-blue`} type="button" disabled={hasPending || !['hot_reload', 'restart_required'].includes(preview.mode)} onClick={() => void run('Submitting reviewed switch', async isCurrent => {
          const token = preview.token; setPreview(null); const applied = await safetyApi.apply(token)
          if (!isCurrent()) return
          setResult(applied); const active = await safetyApi.status(); if (isCurrent()) setStatus(active)
        })}>{preview.mode === 'restart_required' ? 'Save for restart' : 'Apply reviewed switch'}</button>
      </section>}
    </fieldset>
    {result && <section role="status" className="border border-dam-border rounded p-4 text-sm"><strong>{labels[(status?.history.find(item => item.id === result.id) ?? result).status]}</strong><p className="text-xs text-dam-muted mt-2">{(status?.history.find(item => item.id === result.id) ?? result).message}</p></section>}
  </main>
}
