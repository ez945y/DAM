/**
 * useDisplayUnit — React Context for the inspector's joint-unit display mode.
 *
 * The runtime always logs joint state in radians (MCAP, API responses, every
 * structured number flowing out of the server).  Whether the user *sees* rad
 * or deg is a display concern that follows the robot's native mode: a
 * degree-native SO-101 (``degrees_mode: true``) is most useful displayed in
 * deg, a rad-native robot stays rad.
 *
 * Provider mounted at the app root fetches once + revalidates on window focus
 * (catches config edits without manual reload).  Consumers call
 * ``useDisplayUnit()`` — no prop drilling, no global mutable cache.
 */
'use client'
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '@/lib/api'
import { parseConfigFromYaml } from '@/lib/templates'

export type DisplayUnit = 'rad' | 'deg'

interface DisplayUnitContextValue {
  readonly unit: DisplayUnit
  readonly refresh: () => Promise<void>
}

const Ctx = createContext<DisplayUnitContextValue>({
  unit: 'rad',
  refresh: async () => {},
})

async function resolveUnit(): Promise<DisplayUnit> {
  try {
    const yaml = await api.getStackfile('.dam_stackfile.yaml').catch(() => '')
    if (!yaml) return 'rad'
    const parsed = parseConfigFromYaml(yaml)
    return parsed.lerobot_degrees_mode ? 'deg' : 'rad'
  } catch {
    return 'rad'
  }
}

export function DisplayUnitProvider({ children }: { children: ReactNode }) {
  const [unit, setUnit] = useState<DisplayUnit>('rad')

  const refresh = useMemo(
    () => async () => {
      const next = await resolveUnit()
      setUnit(next)
    },
    [],
  )

  useEffect(() => {
    let cancelled = false
    resolveUnit().then((u) => {
      if (!cancelled) setUnit(u)
    })
    const onFocus = () => {
      void refresh()
    }
    window.addEventListener('focus', onFocus)
    return () => {
      cancelled = true
      window.removeEventListener('focus', onFocus)
    }
  }, [refresh])

  const value = useMemo<DisplayUnitContextValue>(() => ({ unit, refresh }), [unit, refresh])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useDisplayUnit(): DisplayUnit {
  return useContext(Ctx).unit
}

/** Imperative refresh — call after writing to the stackfile. */
export function useDisplayUnitRefresh(): () => Promise<void> {
  return useContext(Ctx).refresh
}
