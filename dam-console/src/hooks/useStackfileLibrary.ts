'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'

export const LIVE = '__live__'
// Shared across the Config and Guard pages so the selected stackfile
// (live vs a named library config) stays in sync wherever you edit it.
const TARGET_KEY = 'dam_stackfile_target'

function liveConfigUrl(): string {
  return `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'}/api/system/config`
}

export interface StackfileLibrary {
  readonly LIVE: string
  readonly target: string
  readonly libNames: string[]
  readonly busy: boolean
  readonly error: string | null
  readonly ok: string | null
  /** Persist whatever the editor currently holds to the active target. */
  save: (content: string) => Promise<void>
  /** Fetch + apply the active target once, after mount/hydration. */
  bootstrap: (onLoaded: (yaml: string) => void) => Promise<void>
  switchTarget: (t: string, onLoaded: (yaml: string) => void) => Promise<void>
  create: (currentYaml: string, onLoaded: (yaml: string) => void) => Promise<void>
  rename: () => Promise<void>
  del: (onLoaded: (yaml: string) => void) => Promise<void>
}

export function useStackfileLibrary(): StackfileLibrary {
  const [target, setTargetState] = useState<string>(LIVE)
  const [libNames, setLibNames] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const targetRef = useRef(LIVE)
  const bootstrappedRef = useRef(false)

  const setTarget = useCallback((t: string) => {
    targetRef.current = t
    setTargetState(t)
    try {
      localStorage.setItem(TARGET_KEY, t)
    } catch {
      /* ignore */
    }
  }, [])

  const flashOk = useCallback((m: string) => {
    setOk(m)
    setTimeout(() => setOk(null), 2500)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const res = await api.listStackfiles()
      setLibNames(res.entries.map((e) => e.name))
    } catch {
      /* backend may still be starting */
    }
  }, [])
  useEffect(() => {
    refresh()
  }, [refresh])

  const fetchContent = useCallback(
    async (t: string, fallback: string): Promise<string> => {
      if (t === LIVE) {
        const res = await fetch(liveConfigUrl())
        return res.ok ? await res.text() : fallback
      }
      return api.getStackfile(t)
    },
    [],
  )

  const save = useCallback(async (content: string) => {
    if (!content) return
    const t = targetRef.current
    if (t === LIVE) await api.saveConfig(content)
    else await api.saveStackfile(t, content)
  }, [])

  const bootstrap = useCallback(
    async (onLoaded: (yaml: string) => void) => {
      if (bootstrappedRef.current) return
      bootstrappedRef.current = true
      let t = LIVE
      try {
        t = localStorage.getItem(TARGET_KEY) || LIVE
      } catch {
        /* ignore */
      }
      setTarget(t)
      try {
        const content = await fetchContent(t, '')
        if (content) onLoaded(content)
      } catch {
        /* leave the editor on its default */
      }
    },
    [fetchContent, setTarget],
  )

  const switchTarget = useCallback(
    async (t: string, onLoaded: (yaml: string) => void) => {
      setError(null)
      setBusy(true)
      try {
        const content = await fetchContent(t, '')
        setTarget(t)
        onLoaded(content)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [fetchContent, setTarget],
  )

  const create = useCallback(
    async (currentYaml: string, onLoaded: (yaml: string) => void) => {
      const name = window.prompt('New stackfile name (letters, digits, . _ -):')?.trim()
      if (!name) return
      setBusy(true)
      setError(null)
      try {
        await api.saveStackfile(name, currentYaml)
        await refresh()
        setTarget(name)
        onLoaded(currentYaml)
        flashOk(`Created ${name}`)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [refresh, setTarget, flashOk],
  )

  const rename = useCallback(async () => {
    const t = targetRef.current
    if (t === LIVE) return
    const next = window.prompt('Rename stackfile to:', t)?.trim()
    if (!next || next === t) return
    setBusy(true)
    setError(null)
    try {
      await api.renameStackfile(t, next)
      await refresh()
      setTarget(next)
      flashOk(`Renamed to ${next}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [refresh, setTarget, flashOk])

  const del = useCallback(
    async (onLoaded: (yaml: string) => void) => {
      const t = targetRef.current
      if (t === LIVE) return
      if (!window.confirm(`Delete stackfile "${t}" from the library?`)) return
      setBusy(true)
      setError(null)
      try {
        await api.deleteStackfile(t)
        await refresh()
        const content = await fetchContent(LIVE, '')
        setTarget(LIVE)
        onLoaded(content)
        flashOk('Deleted')
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [refresh, fetchContent, setTarget, flashOk],
  )

  return { LIVE, target, libNames, busy, error, ok, save, bootstrap, switchTarget, create, rename, del }
}
