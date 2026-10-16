'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { DraftResult, SafetyComponent, SafetyPlan } from '@/lib/safety'

export const SAFETY_DRAFT_KEY = 'dam_safety_studio_draft_v1'
export type DeclarationText = Record<'observation_keys' | 'action_units' | 'joint_names', string>
export interface SafetyDraft {
  version: 1
  savedAt: string
  plan: SafetyPlan
  boundaryEdits: Record<number, string>
  description: string
  generatedDraft: DraftResult | null
  exportName: string
  declarations: DeclarationText
}

export function emptySafetyPlan(): SafetyPlan {
  return { name: 'my-safety-plan', task: 'default', base_yaml: '', components: [], contract: { observation_keys: [] } }
}
export function declarationText(plan: SafetyPlan): DeclarationText {
  return {
    observation_keys: plan.contract?.observation_keys.join(', ') ?? '',
    action_units: plan.contract?.action_units?.join(', ') ?? '',
    joint_names: plan.contract?.joint_names?.join(', ') ?? '',
  }
}
function emptyDraft(): SafetyDraft {
  const plan = emptySafetyPlan()
  return { version: 1, savedAt: '', plan, boundaryEdits: {}, description: '', generatedDraft: null, exportName: '', declarations: declarationText(plan) }
}
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string')
function component(value: unknown): value is SafetyComponent {
  return object(value) && typeof value.name === 'string' && ['baseline', 'scene', 'task', 'mode'].includes(String(value.kind)) && object(value.boundaries)
    && Object.values(value.boundaries).every(boundary => object(boundary) && typeof boundary.layer === 'string' && typeof boundary.type === 'string' && Array.isArray(boundary.nodes)
      && boundary.nodes.every(node => object(node) && (typeof node.callback === 'string' || node.callback === null) && object(node.params)))
}
function plan(value: unknown): value is SafetyPlan {
  if (!object(value) || typeof value.name !== 'string' || typeof value.task !== 'string' || typeof value.base_yaml !== 'string' || !Array.isArray(value.components) || !value.components.every(component)) return false
  const contract = value.contract
  return contract === undefined || (object(contract) && strings(contract.observation_keys)
    && (contract.frame === undefined || typeof contract.frame === 'string')
    && (contract.action_units === undefined || strings(contract.action_units))
    && (contract.joint_names === undefined || strings(contract.joint_names)))
}
function generatedDraft(value: unknown): value is DraftResult | null {
  return value === null || (object(value) && Array.isArray(value.components) && value.components.every(component)
    && strings(value.unresolved) && typeof value.explanation === 'string' && value.runtime_verified === false)
}

/** Validate displayable drafts, not executable configuration: incomplete JSON is intentionally retained. */
function readDraft(raw: string): SafetyDraft {
  const value: unknown = JSON.parse(raw)
  if (!object(value) || value.version !== 1 || typeof value.savedAt !== 'string' || !plan(value.plan)
    || !object(value.boundaryEdits) || !Object.entries(value.boundaryEdits).every(([key, text]) => /^\d+$/.test(key) && Number(key) < (value.plan as SafetyPlan).components.length && typeof text === 'string')
    || typeof value.description !== 'string' || typeof value.exportName !== 'string' || !generatedDraft(value.generatedDraft)
    || !object(value.declarations) || !['observation_keys', 'action_units', 'joint_names'].every(key => typeof (value.declarations as Record<string, unknown>)[key] === 'string')) {
    throw new Error('The saved draft has an unsupported or damaged format.')
  }
  // Copy only authoring fields; a stored preview/token/compiled result is never trusted or restored.
  return { version: 1, savedAt: value.savedAt, plan: value.plan, boundaryEdits: value.boundaryEdits as Record<number, string>, description: value.description, generatedDraft: value.generatedDraft, exportName: value.exportName, declarations: value.declarations as DeclarationText }
}

export function useSafetyDraft() {
  const [draft, setDraft] = useState<SafetyDraft>(emptyDraft)
  const current = useRef(draft)
  const [ready, setReady] = useState(false)
  const [restored, setRestored] = useState(false)
  const [storageError, setStorageError] = useState('')
  const [savedAt, setSavedAt] = useState('')
  const edited = useRef(false)

  const restore = useCallback(() => {
    try {
      const raw = localStorage.getItem(SAFETY_DRAFT_KEY)
      if (raw !== null) {
        const recovered = readDraft(raw)
        current.current = recovered
        setDraft(recovered)
        setSavedAt(recovered.savedAt)
        setRestored(true)
      }
      setStorageError('')
    } catch (error) {
      setStorageError(`Local draft could not be restored: ${error instanceof Error ? error.message : String(error)} Existing stored data has not been overwritten.`)
    } finally { setReady(true) }
  }, [])
  useEffect(restore, [restore])

  const persist = useCallback((next: SafetyDraft) => {
    try {
      localStorage.setItem(SAFETY_DRAFT_KEY, JSON.stringify(next))
      setSavedAt(next.savedAt)
      setStorageError('')
    } catch (error) {
      setStorageError(`Local draft saving is unavailable: ${error instanceof Error ? error.message : String(error)} Keep this page open and save the plan to the library when possible. Pending JSON edits are only in this page until local saving succeeds.`)
    }
  }, [])

  const edit = useCallback((change: Partial<SafetyDraft> | ((previous: SafetyDraft) => Partial<SafetyDraft>)) => {
    edited.current = true
    const patch = typeof change === 'function' ? change(current.current) : change
    const next: SafetyDraft = { ...current.current, ...patch, version: 1, savedAt: new Date().toISOString() }
    current.current = next
    setDraft(next)
    // Save in the edit handler, without a debounce/effect window that can lose the last keystroke on refresh.
    persist(next)
  }, [persist])

  return { draft, ready, restored, storageError, savedAt, edit, retrySave: () => edited.current ? persist(current.current) : restore() }
}
