import { useState, useEffect, useMemo } from 'react'
import type { GuardStatus, GuardDecision, BoundaryConfig } from '@/lib/types'
import { ShieldCheck, Terminal, ShieldAlert, ShieldX, AlertOctagon, Shield, ChevronRight, ChevronDown } from 'lucide-react'

export const DEC_CONFIG: Record<GuardDecision | 'STANDBY', {
  color: string; bg: string; border: string;
  Icon: React.ComponentType<{ size?: number | string; className?: string }>
}> = {
  PASS:    { color: 'text-dam-green',  bg: 'bg-green-950/40',  border: 'border-l-dam-green',  Icon: (props) => <ShieldCheck {...props} fill="currentColor" fillOpacity={0.3} /> },
  CLAMP:   { color: 'text-dam-blue',   bg: 'bg-blue-950/40',   border: 'border-l-dam-blue',   Icon: (props) => <ShieldAlert {...props} fill="currentColor" fillOpacity={0.2} /> },
  REJECT:  { color: 'text-dam-orange', bg: 'bg-orange-950/40', border: 'border-l-dam-orange', Icon: (props) => <ShieldX {...props} fill="currentColor" fillOpacity={0.2} /> },
  FAULT:   { color: 'text-dam-red',    bg: 'bg-red-950/40',    border: 'border-l-dam-red',    Icon: (props) => <AlertOctagon {...props} fill="currentColor" fillOpacity={0.2} /> },
  STANDBY: { color: 'text-dam-muted',  bg: 'bg-dam-surface-3', border: 'border-l-dam-border', Icon: Shield       },
}

const LAYER_COLORS: Record<string, string> = {
  L0: 'text-purple-400 bg-purple-950/50 border-purple-900',
  L1: 'text-dam-blue   bg-blue-950/50   border-blue-900',
  L2: 'text-dam-blue bg-blue-950/50 border-blue-900',
  L3: 'text-dam-orange bg-orange-950/50 border-orange-900',
  L4: 'text-dam-red    bg-red-950/50    border-red-900',
}

export function GuardTable({ 
  guards, 
  activeTask, 
  activeBoundaries = [],
  allBoundaryConfigs = []
}: { 
  guards: GuardStatus[];
  activeTask?: string | null;
  activeBoundaries?: string[];
  allBoundaryConfigs?: BoundaryConfig[];
}) {
  const [expandedLayers, setExpandedLayers] = useState<Set<string>>(new Set())

  const toggleLayer = (layer: string) => {
    setExpandedLayers(prev => {
      const next = new Set(prev)
      if (next.has(layer)) next.delete(layer)
      else next.add(layer)
      return next
    })
  }

  const expectedGuards = useMemo(() => {
    if (!activeBoundaries || activeBoundaries.length === 0) {
      return []
    }

    const expected: {boundaryName: string, nodeId: string, layer: string, decision: 'STANDBY'}[] = []
    for (const bname of activeBoundaries) {
      const cfg = allBoundaryConfigs.find(c => c.name === bname)
      if (cfg) {
        if (cfg.nodes && cfg.nodes.length > 0) {
          for (const node of cfg.nodes) {
            expected.push({ 
              boundaryName: bname,
              nodeId: node.node_id || bname, 
              layer: cfg.layer || 'L1',
              decision: 'STANDBY' 
            })
          }
        } else {
          expected.push({ 
            boundaryName: bname,
            nodeId: bname, 
            layer: cfg.layer || 'L1',
            decision: 'STANDBY' 
          })
        }
      }
    }
    return expected
  }, [activeBoundaries, allBoundaryConfigs])

  // Merge runtime guards atop expected configuration
  const mergedMap = new Map<string, GuardStatus | { name: string, boundaryName: string, layer: string, decision: GuardDecision | 'STANDBY', reason?: string }>()
  
  // Initialize with placeholders
  for (const exp of expectedGuards) {
    mergedMap.set(exp.boundaryName, { ...exp, name: exp.boundaryName })
  }

  // Layer mapping for better grouping
  for (const g of guards) {
      // Standardize layer name to 'LX' format
      let l = String(g.layer || '')
      if (!l.startsWith('L') && !isNaN(Number(l))) l = 'L' + l
      
      const gWithFixedLayer = { ...g, layer: l }

      // Enhanced Matching Logic:
      // 1. Match by specific Node ID (priority)
      // 2. Match by Boundary Name (fallback)
      let matchingPlaceholder = expectedGuards.find(e => e.nodeId === g.name || e.boundaryName === g.name)
      
      if (!matchingPlaceholder) {
        matchingPlaceholder = expectedGuards.find(e => g.name.startsWith(e.boundaryName + '_'))
      }

      if (matchingPlaceholder) {
        mergedMap.set(matchingPlaceholder.boundaryName, gWithFixedLayer)
      } else {
        // Add as a standalone guard if no matching placeholder found
        mergedMap.set(g.name, gWithFixedLayer)
      }
  }

  const mergedList = Array.from(mergedMap.values())

  const LAYER_TITLES: Record<string, string> = {
    'L0': 'PERCEPTION (OOD)',
    'L1': 'PREFLIGHT SIMULATION',
    'L2': 'MOTION SAFETY',
    'L3': 'TASK EXECUTION',
    'L4': 'HARDWARE MONITORING'
  }

  const sorted = [...mergedList].sort((a, b) => a.layer.localeCompare(b.layer) || a.name.localeCompare(b.name))

  // Group by layer
  const grouped: Record<string, typeof mergedList> = {}
  
  // Ensure L0-L4 always exist for a professional "pre-flight" look
  const coreLayers = ['L0', 'L1', 'L2', 'L3', 'L4']
  for (const layer of coreLayers) {
    if (!grouped[layer]) grouped[layer] = []
  }

  for (const g of sorted) {
    if (!grouped[g.layer]) grouped[g.layer] = []
    grouped[g.layer].push(g)
  }

  // Calculate active counts per layer based on LIVE runtime guards (standardize layer format)
  const activeCountByLayer = guards.reduce((acc, g) => {
    let l = String(g.layer || '')
    if (!l.startsWith('L') && !isNaN(Number(l))) l = 'L' + l
    if (l.startsWith('L')) {
      acc[l] = (acc[l] || 0) + 1
    }
    return acc
  }, {} as Record<string, number>)

  if (!guards.length && !expectedGuards.length) {
    return (
      <div className="flex flex-col items-center justify-center py-10 opacity-50 border border-dashed border-dam-border/40 rounded bg-dam-surface-2/30">
        <Shield size={32} className="text-dam-muted mb-2" />
        <p className="text-dam-muted text-xs uppercase font-black tracking-widest">No guards configured or active</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {Object.entries(grouped).map(([layer, items]) => {
        const layerCls = LAYER_COLORS[layer] ?? 'text-dam-muted bg-dam-surface-3 border-dam-border'
        const layerTextColor = layerCls.split(' ')[0]
        const hasItems = items.length > 0
        
        // Compute group decision
        let groupDecision: GuardDecision | 'STANDBY' = 'STANDBY'
        if (hasItems) {
          for (const it of items) {
             if (it.decision === 'FAULT') groupDecision = 'FAULT'
             else if (it.decision === 'REJECT' && groupDecision !== 'FAULT') groupDecision = 'REJECT'
             else if (it.decision === 'CLAMP' && !['FAULT','REJECT'].includes(groupDecision)) groupDecision = 'CLAMP'
             else if (it.decision === 'PASS' && groupDecision === 'STANDBY') groupDecision = 'PASS'
          }
        }
        
        const dcGroup = DEC_CONFIG[groupDecision] ?? DEC_CONFIG.STANDBY
        const { Icon } = dcGroup

        const isExpanded = expandedLayers.has(layer)

        return (
          <div key={layer} className={`border border-dam-border/40 rounded bg-dam-surface-2 transition-all duration-300 ${!hasItems ? 'opacity-30 grayscale' : (dcGroup.bg === 'bg-dam-surface-3' ? '' : dcGroup.bg.replace('/40', '/5'))}`}>
            {/* Group Header */}
            <div 
              className={`flex items-center gap-3 px-3 py-2 border-l-2 ${dcGroup.border} transition-colors cursor-pointer hover:bg-dam-surface-3/30 select-none group/row`}
              onClick={() => hasItems && toggleLayer(layer)}
            >
              <div className="flex items-center justify-center w-5">
                {hasItems ? (isExpanded ? <ChevronDown size={14} className="text-dam-muted group-hover/row:text-dam-blue" /> : <ChevronRight size={14} className="text-dam-muted group-hover/row:text-dam-blue" />) : null}
              </div>
              
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <span className={`px-1.5 py-0.5 rounded-[4px] text-[9px] font-black border uppercase shrink-0 ${layerCls}`}>
                  {layer}
                </span>
                <span className="text-dam-text text-[11px] font-black tracking-wider uppercase truncate opacity-90">
                  {LAYER_TITLES[layer]}
                </span>
              </div>

              <div className="flex items-center gap-4 shrink-0 px-2">
                <div className="flex flex-row items-baseline space-x-1">
                  <span className="text-[8px] font-black text-dam-muted/50 uppercase tracking-tighter">active</span>
                  <span className={`text-[11px] font-mono font-black ${activeCountByLayer[layer] > 0 ? 'text-dam-green' : 'text-dam-muted/40'}`}>
                    {activeCountByLayer[layer] || 0}
                  </span>
                </div>
                
                <div className="flex flex-col items-end -space-y-0.5 min-w-[60px]">
                   <span className={`text-[10px] font-black uppercase tracking-widest ${dcGroup.color}`}>
                    {groupDecision}
                  </span>
                </div>
              </div>
            </div>
            
            {/* List Details */}
            {isExpanded && (
              <div className="border-t border-dam-border/30 bg-black/30 divide-y divide-dam-border/10 animate-in fade-in slide-in-from-top-1 duration-200">
                {items.map(g => {
                  const dc = DEC_CONFIG[g.decision] ?? DEC_CONFIG.PASS
                  const isLive = guards.some(rg => rg.name === g.name)
                  return (
                    <div key={g.name} className="flex items-center gap-3 px-4 py-2 pl-12 hover:bg-white/[0.02] transition-colors">
                      <div className={`w-1 h-1 rounded-full ${isLive ? 'bg-dam-green shadow-[0_0_5px_rgba(16,185,129,0.5)]' : 'bg-dam-muted/20'}`} />
                      <span className="text-dam-muted text-[11px] font-mono flex-1 truncate opacity-70">
                        {g.name}
                      </span>
                      <span className={`text-[9px] font-black uppercase tracking-widest min-w-[50px] text-right ${dc.color}`}>
                        {g.decision}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )

      })}
    </div>
  )
}

