'use client'

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { safetyComparisonApi, type SafetyComparison, type SafetyRegression, type RegressionResult, type CandidateOutcome } from '@/lib/safety-comparison-api'

const inputClass = 'rounded border border-dam-border bg-dam-surface-2 px-3 py-2 text-sm text-dam-text'
const buttonClass = 'rounded bg-dam-blue px-3 py-2 text-sm text-white disabled:opacity-40'
const message = (error: unknown) => error instanceof Error ? error.message : String(error)

function Outcome({ value }: { value: CandidateOutcome }) {
  return <div>
    <span className={value.status !== 'evaluated' || value.decision === 'FAULT' ? 'text-amber-400' : ''}>{value.decision ?? value.status.toUpperCase()}</span>
    {value.correction_l2 != null && <span className="ml-2 text-dam-muted">correction {value.correction_l2.toPrecision(4)}</span>}
    {value.issues.map(issue => <p key={issue} className="max-w-lg text-xs text-amber-400">{issue}</p>)}
    {value.guards?.filter(guard => guard.decision !== 'PASS').map((guard, i) => <p key={i} className="text-xs text-dam-muted">{guard.name}: {guard.reason}</p>)}
  </div>
}

export function SafetyComparisonPanel() {
  const [sessions, setSessions] = useState<string[]>([])
  const [stacks, setStacks] = useState<string[]>([])
  const [mcap, setMcap] = useState('')
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [result, setResult] = useState<SafetyComparison | null>(null)
  const [cases, setCases] = useState<SafetyRegression[]>([])
  const [runs, setRuns] = useState<Record<string, RegressionResult>>({})
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const refreshVersion = useRef(0)
  const operationActive = useRef(false)
  const mounted = useRef(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [page, setPage] = useState(0)
  const [caseName, setCaseName] = useState('')
  const [side, setSide] = useState<'left' | 'right'>('right')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(0)
  const [expected, setExpected] = useState('')

  const refresh = useCallback(async () => {
    if (operationActive.current) return
    const version = ++refreshVersion.current
    setRefreshing(true)
    setError('')
    const responses = await Promise.allSettled([api.listMcapSessions(), api.listStackfiles(), safetyComparisonApi.cases()])
    if (!mounted.current || version !== refreshVersion.current) return
    setRefreshing(false)
    const [sessionResult, stackResult, caseResult] = responses
    if (sessionResult.status === 'fulfilled') {
      const names = sessionResult.value.sessions.map(session => session.filename)
      setSessions(names); setMcap(current => names.includes(current) ? current : names[0] ?? '')
    }
    if (stackResult.status === 'fulfilled') {
      const names = [...(stackResult.value.live.exists ? ['__live__'] : []), ...stackResult.value.entries.map(stack => stack.name)]
      setStacks(names); setLeft(current => names.includes(current) ? current : names[0] ?? '')
      setRight(current => names.includes(current) ? current : names[1] ?? names[0] ?? '')
    }
    if (caseResult.status === 'fulfilled') {
      setCases(caseResult.value.cases)
      if (caseResult.value.errors.length) setError(`Some saved cases could not be read: ${JSON.stringify(caseResult.value.errors)}`)
    }
    const errors = responses.filter(response => response.status === 'rejected').map(response => message((response as PromiseRejectedResult).reason))
    if (errors.length) setError(errors.join('; '))
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => { mounted.current = false; refreshVersion.current += 1 }
  }, [refresh])

  function beginOperation() {
    if (operationActive.current) return false
    operationActive.current = true
    // A pending list request must not overwrite a newly saved case or result.
    refreshVersion.current += 1
    setRefreshing(false); setBusy(true)
    return true
  }
  function endOperation() {
    operationActive.current = false
    if (mounted.current) setBusy(false)
  }

  async function compare() {
    if (!beginOperation()) return
    setError(''); setNotice(''); setResult(null)
    try {
      const comparison = await safetyComparisonApi.compare(mcap, left, right)
      if (!mounted.current) return
      setResult(comparison); setPage(0)
      setStart(comparison.cycles[0]?.cycle ?? 0); setEnd(comparison.cycles.at(-1)?.cycle ?? 0)
    } catch (err) { if (mounted.current) setError(message(err)) } finally { endOperation() }
  }
  async function save() {
    if (!result || !beginOperation()) return
    setError(''); setNotice('')
    try {
      const saved = await safetyComparisonApi.save({ comparison_id: result.id, name: caseName, side, cycle_start: start, cycle_end: end, ...(expected ? { expected_decision: expected } : {}) })
      if (!mounted.current) return
      setCases(current => [...current.filter(item => item.id !== saved.id), saved]); setNotice(`Saved “${saved.name}” with its recording fingerprint and configuration snapshot.`)
    } catch (err) { if (mounted.current) setError(message(err)) } finally { endOperation() }
  }
  async function run(id: string) {
    if (!beginOperation()) return
    setError('')
    try { const value = await safetyComparisonApi.run(id); if (mounted.current) setRuns(current => ({ ...current, [id]: value })) }
    catch (err) { if (mounted.current) setError(message(err)) } finally { endOperation() }
  }
  const rows = result ? (showAll ? result.cycles : result.cycles.filter(row => row.changed || !row.comparable)) : []
  const maxPage = Math.max(0, Math.ceil(rows.length / 25) - 1)
  const selectedRows = result?.cycles.filter(row => row.cycle >= start && row.cycle <= end) ?? []
  const validRange = Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end >= start
    && selectedRows.length === end - start + 1
    && selectedRows.every((row, index) => row.cycle === start + index && row[side].status === 'evaluated')

  return <section className="mb-8 space-y-4 rounded-lg border border-dam-border bg-dam-surface-1 p-5" aria-label="Safety A/B comparison">
    <div className="flex items-start justify-between gap-4">
      <div><h2 className="font-semibold text-dam-text">Safety A/B comparison & regression cases</h2>
        <p className="mt-1 text-sm text-dam-muted">Evaluate two configurations on every recorded input. Offline, synchronous evaluation; no robot commands are sent.</p></div>
      <button className={inputClass} onClick={() => void refresh()} disabled={busy || refreshing}>{refreshing ? 'Refreshing…' : 'Refresh choices'}</button>
    </div>
    <div className="grid gap-3 md:grid-cols-3">
      <label className="text-xs text-dam-muted">Recording<select aria-label="Comparison recording" disabled={busy} value={mcap} onChange={event => setMcap(event.target.value)} className={`${inputClass} mt-1 w-full`}><option value="">Select recording</option>{sessions.map(name => <option key={name}>{name}</option>)}</select></label>
      <label className="text-xs text-dam-muted">Left configuration<select aria-label="Left configuration" disabled={busy} value={left} onChange={event => setLeft(event.target.value)} className={`${inputClass} mt-1 w-full`}><option value="">Select configuration</option>{stacks.map(name => <option key={name} value={name}>{name === '__live__' ? 'Live configuration snapshot' : name}</option>)}</select></label>
      <label className="text-xs text-dam-muted">Right configuration<select aria-label="Right configuration" disabled={busy} value={right} onChange={event => setRight(event.target.value)} className={`${inputClass} mt-1 w-full`}><option value="">Select configuration</option>{stacks.map(name => <option key={name} value={name}>{name === '__live__' ? 'Live configuration snapshot' : name}</option>)}</select></label>
    </div>
    <div className="flex items-center gap-3"><button className={buttonClass} disabled={busy || !mcap || !left || !right} onClick={() => void compare()}>{busy ? 'Working…' : 'Compare both configurations'}</button><span className="text-xs text-dam-muted">Up to 64 MiB / 20,000 cycles; 60 seconds per candidate.</span></div>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    {notice && <p role="status" className="text-sm text-green-400">{notice}</p>}
    {result && <div className="space-y-4">
      <p role="status" className={result.status === 'complete' ? 'text-green-400' : 'text-amber-400'}>{result.status.toUpperCase()} · {result.compared}/{result.total} comparable cycles · {result.difference_count} differences · {result.error_cycles} incomplete cycles</p>
      <p className="text-xs text-dam-muted">{result.mcap} · Left: {result.configs.left.name} ({result.configs.left.sha256.slice(0, 10)}) · Right: {result.configs.right.name} ({result.configs.right.sha256.slice(0, 10)})</p>
      <div className="grid gap-3 md:grid-cols-2">{(['left', 'right'] as const).map(candidate => <div key={candidate} className="rounded border border-dam-border p-3 text-sm"><b>{candidate.toUpperCase()}</b><p className="mt-1 font-mono">{Object.entries(result.distributions[candidate]).map(([decision, count]) => `${decision}: ${count}`).join(' · ')}</p></div>)}</div>
      <details className="text-xs text-dam-muted"><summary>Evaluation limits and assumptions</summary><ul className="mt-2 list-disc space-y-1 pl-5">{result.limitations.map(note => <li key={note}>{note}</li>)}</ul></details>
      {result.top_differences.length > 0 && <p className="text-xs text-dam-muted">Largest differences (decision changes first): {result.top_differences.slice(0, 5).map(row => `#${row.cycle} ${row.left.decision} → ${row.right.decision}${row.output_delta_l2 != null ? ` (Δ ${row.output_delta_l2.toPrecision(3)})` : ''}`).join(' · ')}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><label><input type="checkbox" checked={showAll} onChange={event => { setShowAll(event.target.checked); setPage(0) }} /> Show all cycles</label><div className="flex items-center gap-2"><button disabled={page === 0} onClick={() => setPage(page - 1)} className={inputClass}>Previous</button><span>{page + 1}/{maxPage + 1}</span><button disabled={page >= maxPage} onClick={() => setPage(page + 1)} className={inputClass}>Next</button></div></div>
      <div className="max-h-96 overflow-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-dam-border"><th className="p-2">Cycle</th><th className="p-2">Left</th><th className="p-2">Right</th><th className="p-2">Output Δ</th></tr></thead><tbody>{rows.slice(page * 25, page * 25 + 25).map(row => <tr key={row.cycle} className="border-b border-dam-border/50"><td className="p-2">{row.cycle}</td><td className="p-2"><Outcome value={row.left} /></td><td className="p-2"><Outcome value={row.right} /></td><td className="p-2 font-mono">{row.output_delta_l2?.toPrecision(4) ?? '—'}</td></tr>)}</tbody></table>{rows.length === 0 && <p className="p-3 text-dam-muted">No differences or incomplete cycles.</p>}</div>
      <fieldset className="space-y-3 rounded border border-dam-border p-3"><legend className="px-2 text-sm">Save an executable regression case</legend>
        <div className="flex flex-wrap gap-2"><input aria-label="Regression name" placeholder="Case name" value={caseName} onChange={event => setCaseName(event.target.value)} className={inputClass} />
          <select aria-label="Regression candidate" value={side} onChange={event => setSide(event.target.value as 'left' | 'right')} className={inputClass}><option value="left">Left snapshot</option><option value="right">Right snapshot</option></select>
          <label className="text-xs">From cycle <input aria-label="Start cycle" type="number" min={0} value={start} onChange={event => setStart(Number(event.target.value))} className={`${inputClass} w-24`} /></label>
          <label className="text-xs">To cycle <input aria-label="End cycle" type="number" min={start} value={end} onChange={event => setEnd(Number(event.target.value))} className={`${inputClass} w-24`} /></label>
          <select aria-label="Expected result" value={expected} onChange={event => setExpected(event.target.value)} className={inputClass}><option value="">Match current decisions & outputs</option>{['PASS', 'CLAMP', 'REJECT', 'FAULT'].map(decision => <option key={decision} value={decision}>Must be {decision}</option>)}</select>
          <button className={buttonClass} disabled={busy || !caseName.trim() || !validRange} onClick={() => void save()}>Save case</button></div>
        {!validRange && <p className="text-xs text-amber-400">Choose a complete range of evaluated cycles for the selected candidate.</p>}
        <p className="text-xs text-dam-muted">Reruns use the saved configuration and verify the MCAP fingerprint, replaying the full prefix to preserve motion history. An explicit decision expectation applies to every cycle in the selected range.</p>
      </fieldset>
    </div>}
    <div className="space-y-2"><h3 className="text-sm font-semibold">Saved regression cases</h3>{cases.length === 0 && <p className="text-sm text-dam-muted">No cases saved yet.</p>}{cases.map(item => <div key={item.id} className="rounded border border-dam-border p-3 text-sm"><div className="flex items-center justify-between gap-3"><span>{item.name} · cycles {item.cycle_start}–{item.cycle_end} · {item.config.name}</span><button className={inputClass} disabled={busy} onClick={() => void run(item.id)}>Rerun case</button></div>{runs[item.id] && <div role="status" className={`mt-2 ${runs[item.id].status === 'pass' ? 'text-green-400' : 'text-amber-400'}`}>{runs[item.id].status.toUpperCase()} · {runs[item.id].checked ?? 0} assertions · {runs[item.id].mismatches.length} mismatches{(runs[item.id].issues.length > 0 || runs[item.id].mismatches.length > 0) && <details><summary>Result details</summary><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(runs[item.id], null, 2)}</pre></details>}</div>}</div>)}</div>
  </section>
}
