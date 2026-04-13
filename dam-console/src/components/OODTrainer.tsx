'use client'

import { useState, useEffect, useRef } from 'react'
import { Database, Loader2, Play, Trash2, Settings2, Info } from 'lucide-react'

export function OODTrainer({
  selectedPath,
  onSelect,
  onSelectMeta,
}: {
  selectedPath?: string,
  onSelect?: (path: string) => void
  /** Called with the full model metadata when a model is selected. */
  onSelectMeta?: (path: string, meta: { backend?: string; bank_path?: string }) => void
}) {
  const [repoId, setRepoId] = useState('MikeChenYZ/soarm-fmb-v2')
  const [backend, setBackend] = useState('memory_bank')
  const [outputName, setOutputName] = useState('ood_model')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [showTrainer, setShowTrainer] = useState(false)
  const [models, setModels] = useState<{name: string, path: string, metadata?: any}[]>([])
  const [progressMsg, setProgressMsg] = useState<string>('')
  const [epochs, setEpochs] = useState(50)
  const [lr, setLr] = useState(0.001)

  const wsRef = useRef<WebSocket | null>(null)
  
  // Use effect to fetch models on load
  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'}/api/ood/models`)
      .then(res => res.json())
      .then(data => setModels(data.models || []))
      .catch(() => {})
  }, [])

  // Reconnection logic
  useEffect(() => {
    let baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'
    const wsUrl = baseUrl.replace(/^http/, 'ws') + '/api/ood/train/ws'
    
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      // Just connecting doesn't start a task, the server will tell us if one exists
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.status === 'exists') {
          // A task is already running, we should show it
          setLoading(true)
          setProgressMsg(msg.message)
          if (msg.config) {
            setRepoId(msg.config.repo_id || repoId)
            setBackend(msg.config.backend || backend)
            setOutputName(msg.config.output_name || outputName)
          }
          setShowTrainer(true)
        } else if (msg.status === 'running') {
          setLoading(true)
          setProgressMsg(msg.message)
        } else if (msg.status === 'success') {
          setResult(msg.result)
          setLoading(false)
          setProgressMsg('')
          // Refresh models list immediately on success
          fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'}/api/ood/models`)
            .then(res => res.json())
            .then(data => setModels(data.models || []))
            .catch(() => {})
        } else if (msg.status === 'error') {
          setError(msg.message)
          setLoading(false)
          setProgressMsg('')
        } else if (msg.status === 'cancelled') {
          setError('Training was cancelled.')
          setLoading(false)
          setProgressMsg('')
        }
      } catch (err) { /* ignore */ }
    }

    ws.onclose = () => {
      // Reconnect after delay?
    }

    return () => ws.close()
  }, [])

  const handleTrain = () => {
    if (loading || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    setLoading(true)
    setError(null)
    setResult(null)
    setProgressMsg('Starting...')

    wsRef.current.send(JSON.stringify({
      action: 'start',
      repo_id: repoId,
      backend: backend,
      output_name: outputName,
      flow_epochs: epochs,
      flow_lr: lr,
    }))
  }

  const handleDeleteModel = async (name: string) => {
    if (!confirm(`Delete model ${name}?`)) return
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'}/api/ood/models/${name.replace('.pt', '')}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        setModels(prev => prev.filter(m => m.name !== name))
      }
    } catch (err) { /* ignore */ }
  }

  const handleCancel = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'cancel' }))
      setProgressMsg('Cancelling...')
    }
  }

  const inputCls =
    'bg-dam-surface-3 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text focus:outline-none focus:border-dam-blue/60 transition-colors w-full'

  return (
    <div className="p-3 space-y-4">
      {/* Model Selector Grid */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <label className="text-[10px] uppercase tracking-widest text-dam-muted font-bold flex items-center gap-2">
            Installed Neural Profiles
            {models.length > 0 && <span className="text-[9px] lowercase font-normal opacity-60">({models.length} profiles discovered)</span>}
          </label>
          <button 
            type="button"
            onClick={() => setShowTrainer(!showTrainer)}
            className={`flex items-center gap-1.5 text-[10px] font-black transition-all px-3 py-1.5 rounded-full border ${
              showTrainer 
                ? 'bg-dam-blue/20 text-dam-blue border-dam-blue/50 shadow-lg shadow-dam-blue/10' 
                : 'bg-white/5 text-dam-muted border-white/5 hover:border-white/10 hover:text-dam-blue'
            }`}
          >
            {showTrainer ? <Trash2 size={10} className="rotate-45" /> : <Play size={10} />}
            {showTrainer ? 'Close Workspace' : 'Train Profile'}
          </button>
        </div>
        
        {models.length === 0 ? (
          <div className="p-8 border border-dashed border-white/5 rounded-xl bg-white/[0.01] text-center">
            <div className="w-10 h-10 rounded-full bg-white/5 flex items-center justify-center mx-auto mb-3 text-dam-muted">
              <Database size={20} />
            </div>
            <p className="text-xs text-dam-muted font-medium">No intelligence profiles found.</p>
            <p className="text-[10px] text-dam-muted/60 mt-1">Start by training a new model from a Hugging Face dataset.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-[300px] overflow-y-auto pr-1 thin-scrollbar py-1">
            {models.map(m => {
              const isSelected = selectedPath === m.path || 
                                (selectedPath && m.path && selectedPath.split('/').pop() === m.path.split('/').pop())

              return (
                <div
                  key={m.path}
                  onClick={() => {
                    onSelect?.(m.path);
                    onSelectMeta?.(m.path, {
                      backend: m.metadata?.backend,
                      bank_path: m.metadata?.bank_path,
                    });
                  }}
                  className={`relative flex flex-col gap-2.5 p-3 rounded-xl border transition-all cursor-pointer group select-none ${
                    isSelected
                      ? 'bg-dam-blue/10 border-dam-blue/50 shadow-lg shadow-dam-blue/5'
                      : 'bg-white/[0.02] border-white/5 hover:border-white/10 hover:bg-white/[0.04]'
                  }`}
                >
                  {/* Selection Glow */}
                  {isSelected && <div className="absolute inset-0 rounded-xl bg-dam-blue/5 animate-pulse pointer-events-none" />}
                  
                  <div className="flex items-start justify-between relative z-10">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={`p-1.5 rounded-lg shrink-0 ${isSelected ? 'bg-dam-blue text-white' : 'bg-white/5 text-dam-muted'}`}>
                        <Database size={12} />
                      </div>
                      <div className="min-w-0">
                        <h5 className={`text-[11px] font-bold truncate ${isSelected ? 'text-dam-blue' : 'text-dam-text'}`}>
                          {m.name.replace('.pt', '')}
                        </h5>
                        <p className="text-[9px] text-dam-muted truncate opacity-60 font-mono">
                          {m.metadata?.backend || 'Generic'}
                        </p>
                      </div>
                    </div>
                    
                    <button 
                      type="button"
                      className="p-1.5 rounded-md text-dam-muted hover:text-dam-red hover:bg-dam-red/10 transition-all opacity-0 group-hover:opacity-100"
                      onClick={(e) => { 
                        e.stopPropagation(); 
                        handleDeleteModel(m.name); 
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  
                  {m.metadata?.repo_id && (
                    <div className="mt-auto pt-2 border-t border-white/5 grid grid-cols-2 gap-2 relative z-10">
                      <div className="flex items-center gap-1 text-[8px] text-dam-muted font-mono truncate">
                        <span className="opacity-40">Src:</span> {m.metadata.repo_id.split('/').pop()}
                      </div>
                      <div className="text-right text-[8px] text-dam-muted font-mono opacity-80">
                        {new Date(m.metadata.timestamp * 1000).toLocaleDateString()}
                      </div>
                    </div>
                  )}

                  {isSelected && (
                    <div className="absolute -top-1.5 -right-1.5 z-20">
                      <div className="bg-dam-blue text-white text-[8px] font-black px-1.5 py-0.5 rounded-full shadow-lg border border-white/20 uppercase tracking-tighter">
                        Active
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {showTrainer && (
        <div className="p-4 rounded-xl border border-dam-blue/20 bg-dam-blue/5 space-y-4 relative overflow-hidden">

          <div className="relative z-10 flex items-center justify-between">
            <h3 className="text-[11px] font-bold uppercase tracking-widest text-dam-blue flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-dam-blue opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-dam-blue"></span>
              </span>
              Neural Training Workspace
            </h3>
            {loading && (
              <div className="flex items-center gap-2 px-2 py-1 bg-dam-blue/10 rounded-lg">
                <Loader2 size={12} className="text-dam-blue animate-spin" />
                <span className="text-[10px] font-bold text-dam-blue font-mono">{progressMsg || 'Processing...'}</span>
              </div>
            )}
          </div>
          
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 relative z-10">
            <div className="space-y-1.5">
              <label className="text-[9px] text-dam-muted font-bold uppercase tracking-tighter ml-1">Dataset Repository (HF)</label>
              <div className="group relative">
                <Database className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dam-muted group-focus-within:text-dam-blue transition-colors" size={12} />
                <input 
                  value={repoId} 
                  onChange={(e) => setRepoId(e.target.value)} 
                  className={`${inputCls} pl-8 h-9 rounded-lg !bg-dam-surface-3/80`} 
                />
              </div>
            </div>
            
            <div className="space-y-1.5">
              <label className="text-[9px] text-dam-muted font-bold uppercase tracking-tighter ml-1">Analysis Architecture</label>
              <div className="relative">
                <Settings2 className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dam-muted" size={12} />
                <select 
                  value={backend} 
                  onChange={(e) => setBackend(e.target.value)} 
                  className={`${inputCls} pl-8 h-9 rounded-lg !bg-dam-surface-3/80 appearance-none`}
                >
                  <option value="memory_bank">Memory Bank (Fastest)</option>
                  <option value="normalizing_flow">Neural Density Flow</option>
                </select>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 pt-2 relative z-10">
            {!loading ? (
              <button
                type="button"
                onClick={handleTrain}
                className="flex items-center gap-2 px-5 py-2.5 bg-dam-blue text-white text-[11px] font-black rounded-lg hover:bg-dam-blue-bright transition-all active:scale-95 shadow-lg shadow-dam-blue/20 uppercase tracking-wide"
              >
                <Play size={14} fill="currentColor" />
                Initialize Profile
              </button>
            ) : (
              <button
                type="button"
                onClick={handleCancel}
                className="flex items-center gap-2 px-5 py-2.5 bg-dam-red text-white text-[11px] font-black rounded-lg hover:bg-red-500 transition-all active:scale-95 shadow-lg shadow-dam-red/20 uppercase tracking-wide"
              >
                <Loader2 size={14} className="animate-spin" />
                Abort Session
              </button>
            )}

            <div className="flex-1 text-right">
              {error && (
                <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-dam-red/10 border border-dam-red/20 rounded-full text-dam-red text-[10px] font-bold">
                  <Info size={10} /> {error}
                </div>
              )}
              {result && (
                <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-dam-green/10 border border-dam-green/20 rounded-full text-dam-green text-[10px] font-bold">
                  Success: Profile Generated
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
