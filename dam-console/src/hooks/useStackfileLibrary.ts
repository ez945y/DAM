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
  readonly ready: boolean
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
  const [busy, setBusyState] = useState(false)
  const [ready, setReadyState] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const targetRef = useRef(LIVE)
  const bootstrappedRef = useRef(false)
  const busyRef = useRef(false)
  const readyRef = useRef(false)

  const setBusy = useCallback((value: boolean) => {
    busyRef.current = value
    setBusyState(value)
  }, [])
  const setReady = useCallback((value: boolean) => {
    readyRef.current = value
    setReadyState(value)
  }, [])

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
    async (t: string): Promise<string> => {
      if (t === LIVE) {
        const res = await fetch(liveConfigUrl(), { cache: 'no-store' })
        // A missing live file is a fresh installation, not a failed read.
        if (res.status === 404) return ''
        if (!res.ok) throw new Error(`Failed to load live config: HTTP ${res.status}`)
        return res.text()
      }
      return api.getStackfile(t)
    },
    [],
  )

  const save = useCallback(async (content: string) => {
    if (!content) return
    if (!readyRef.current || busyRef.current || target !== targetRef.current) {
      throw new Error('Wait for the selected stackfile to finish loading before saving')
    }
    try {
      if (target === LIVE) await api.saveConfig(content)
      else await api.saveStackfile(target, content)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      throw e
    }
  }, [target])

  const bootstrap = useCallback(
    async (onLoaded: (yaml: string) => void) => {
      if (bootstrappedRef.current) return
      bootstrappedRef.current = true
      setBusy(true)
      let t = LIVE
      try {
        t = localStorage.getItem(TARGET_KEY) || LIVE
      } catch {
        /* ignore */
      }
      setTarget(t)
      try {
        const content = await fetchContent(t)
        onLoaded(content)
        setReady(true)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [fetchContent, setTarget, setBusy, setReady],
  )

  const switchTarget = useCallback(
    async (t: string, onLoaded: (yaml: string) => void) => {
      if (busyRef.current) return
      setError(null)
      setBusy(true)
      try {
        const content = await fetchContent(t)
        onLoaded(content)
        setTarget(t)
        setReady(true)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [fetchContent, setTarget, setBusy, setReady],
  )

  const create = useCallback(
    async (currentYaml: string, onLoaded: (yaml: string) => void) => {
      if (busyRef.current) return
      const name = window.prompt('New stackfile name (letters, digits, . _ -):')?.trim()
      if (!name) return
      setBusy(true)
      setError(null)
      try {
        await api.createStackfile(name, currentYaml)
        await refresh()
        setTarget(name)
        onLoaded(currentYaml)
        setReady(true)
        flashOk(`Created ${name}`)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [refresh, setTarget, flashOk, setBusy, setReady],
  )

  const rename = useCallback(async () => {
    if (busyRef.current) return
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
  }, [refresh, setTarget, flashOk, setBusy])

  const del = useCallback(
    async (onLoaded: (yaml: string) => void) => {
      if (busyRef.current) return
      const t = targetRef.current
      if (t === LIVE) return
      if (!window.confirm(`Delete stackfile "${t}" from the library?`)) return
      setBusy(true)
      setError(null)
      try {
        // Read the replacement before deleting, so a failed read leaves the
        // current entry and editor intact.
        const content = await fetchContent(LIVE)
        await api.deleteStackfile(t)
        await refresh()
        onLoaded(content)
        setTarget(LIVE)
        setReady(true)
        flashOk('Deleted')
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [refresh, fetchContent, setTarget, flashOk, setBusy, setReady],
  )

  return { LIVE, target, libNames, busy, ready, error, ok, save, bootstrap, switchTarget, create, rename, del }
}
