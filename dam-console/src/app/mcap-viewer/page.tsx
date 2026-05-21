"use client";
import React, { useEffect, useState, Suspense, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type {
  McapSessionSummary,
  McapSessionDetail,
  McapCycle,
} from "@/lib/api";
import { McapTimelineView } from "@/components/McapTimelineView";
import { McapCycleInspector } from "@/components/McapCycleInspector";
import { McapCameraPlayer } from "@/components/McapCameraPlayer";
import { PageShell } from "@/components/PageShell";
import {
  Film,
  Download,
  Loader2,
  AlertCircle,
  FileText,
  Activity,
  AlertTriangle,
  ShieldAlert,
  Clock,
  Archive,
} from "lucide-react";

// ── Session list card ─────────────────────────────────────────────────────────

function SessionCard({
  session,
  detail,
  selected,
  checked,
  onClick,
  onToggleSelect,
}: {
  session: McapSessionSummary;
  detail: McapSessionDetail | null;
  selected: boolean;
  checked: boolean;
  onClick: () => void;
  onToggleSelect: (checked: boolean) => void;
}) {
  const violations = detail?.stats.violation_cycles ?? 0;
  const clamps = detail?.stats.clamp_cycles ?? 0;
  const created = new Date(session.created_at * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <button
      onClick={onClick}
      className={[
        "w-full text-left p-3 rounded-lg border transition-all duration-150 space-y-2",
        selected
          ? "bg-dam-blue/10 border-dam-blue/40 shadow-sm"
          : "bg-dam-surface-2 border-dam-border/60 hover:border-dam-blue/30 hover:bg-dam-surface-1",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5">
        <input
          aria-label={`Select ${session.filename}`}
          type="checkbox"
          checked={checked}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onToggleSelect(e.target.checked)}
          className="h-4 w-4 accent-dam-muted shrink-0"
        />
        {detail && (
          <span className="flex items-center gap-1 text-[10px] text-dam-muted bg-dam-surface-1 px-1.5 py-0.5 rounded border border-dam-border">
            <Activity size={9} />
            {detail.stats.total_cycles.toLocaleString()}
          </span>
        )}
        {clamps > 0 && (
          <span className="flex items-center gap-1 text-[10px] text-dam-blue bg-blue-500/10 px-1.5 py-0.5 rounded border border-blue-500/20">
            <ShieldAlert size={9} />
            {clamps}
          </span>
        )}
        {violations > 0 && (
          <span className="flex items-center gap-1 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded border border-red-500/20">
            <AlertTriangle size={9} />
            {violations}
          </span>
        )}
      </div>

      <div className="flex items-start gap-2">
        <Film
          size={13}
          className={`shrink-0 mt-0.5 ${selected ? "text-dam-blue" : "text-dam-muted"}`}
        />
        <div className="flex-1 min-w-0">
          <p className="font-mono text-[11px] font-semibold text-dam-text truncate">
            {session.filename}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-dam-muted">
        <span className="truncate" suppressHydrationWarning>
          {created}
        </span>
        <span className="ml-auto font-mono shrink-0">
          {session.size_mb.toFixed(1)} MB
        </span>
        {detail && detail.stats.duration_sec > 0 && (
          <span className="flex items-center gap-1 shrink-0">
            <Clock size={9} />
            {detail.stats.duration_sec.toFixed(1)}s
          </span>
        )}
      </div>
    </button>
  );
}

// ── Session header bar ────────────────────────────────────────────────────────

function SessionHeader({
  session,
  detail,
  onArchived,
}: {
  session: McapSessionSummary;
  detail: McapSessionDetail;
  onArchived: (filenames: string[]) => void;
}) {
  const [metaOpen, setMetaOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const meta = detail.metadata ?? {};
  const hasMeta = Object.keys(meta).length > 0;

  function handleDeleteClick() {
    setDeleteError(null);
    setDeleteConfirm(true);
  }

  function handleDeleteCancel() {
    setDeleteConfirm(false);
    setDeleteError(null);
  }

  function handleDeleteConfirm() {
    setDeleting(true);
    setDeleteError(null);
    api
      .deleteMcapSession(session.filename)
      .then(() => {
        onArchived([session.filename]);
      })
      .catch((e: Error) => {
        setDeleteError(e.message);
        setDeleteConfirm(false);
        setDeleting(false);
      });
  }

  return (
    <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-4 py-3 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Film size={14} className="text-dam-blue shrink-0" />
          <span className="font-mono text-sm font-bold text-dam-text truncate">
            {session.filename}
          </span>
          <span className="text-xs text-dam-muted shrink-0">
            {session.size_mb.toFixed(1)} MB
          </span>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-dam-muted bg-dam-surface-1 px-2 py-1 rounded border border-dam-border">
            {detail.stats.total_cycles.toLocaleString()} cycles
          </span>
          {detail.stats.violation_cycles > 0 && (
            <span className="text-xs text-red-400 bg-red-500/10 px-2 py-1 rounded border border-red-500/20">
              {detail.stats.violation_cycles} violations
            </span>
          )}
          {detail.stats.clamp_cycles > 0 && (
            <span className="text-xs text-dam-blue bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">
              {detail.stats.clamp_cycles} clamps
            </span>
          )}
          {detail.stats.duration_sec > 0 && (
            <span className="text-xs text-dam-muted">
              {detail.stats.duration_sec.toFixed(1)} s
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {hasMeta && (
            <button
              onClick={() => setMetaOpen((v) => !v)}
              className="text-[10px] text-dam-muted hover:text-dam-text border border-dam-border px-2 py-1 rounded hover:border-dam-blue/30 transition-colors"
            >
              {metaOpen ? "Hide" : "Metadata"}
            </button>
          )}
          <a
            href={api.mcapDownloadUrl(session.filename)}
            className="flex items-center gap-1.5 text-[10px] font-bold text-dam-blue bg-dam-blue/10 hover:bg-dam-blue/20 border border-dam-blue/30 px-2.5 py-1 rounded transition-colors"
          >
            <Download size={11} />
            Download
          </a>
          {deleteConfirm ? (
            <div className="flex items-center gap-1">
              <button
                onClick={handleDeleteConfirm}
                disabled={deleting}
                className="text-[10px] font-bold text-white bg-red-600 hover:bg-red-700 disabled:opacity-50 px-2 py-1 rounded transition-colors"
              >
                {deleting ? "…" : "Move"}
              </button>
              <button
                onClick={handleDeleteCancel}
                disabled={deleting}
                className="text-[10px] text-dam-muted hover:text-dam-text border border-dam-border px-2 py-1 rounded transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={handleDeleteClick}
              className="flex items-center justify-center p-1 text-dam-muted hover:text-red-400 hover:bg-red-500/10 border border-transparent hover:border-red-500/50 rounded transition-colors"
              title="Move to _trash"
            >
              <Archive size={12} />
            </button>
          )}
        </div>
      </div>

      {deleteError && (
        <div className="flex items-center gap-2 text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded px-3 py-1.5">
          <ShieldAlert size={11} className="shrink-0" />
          {deleteError}
        </div>
      )}

      {metaOpen && hasMeta && (
        <div className="pt-2 border-t border-dam-border/40 grid grid-cols-2 sm:grid-cols-3 gap-1">
          {Object.entries(meta).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5 text-[10px]">
              <span className="text-dam-muted shrink-0">{k}:</span>
              <span className="font-mono text-dam-text/80 truncate" title={v}>
                {v}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main content (needs Suspense for useSearchParams) ─────────────────────────

function McapViewerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // URL-driven state
  const [selectedFilename, setSelectedFilename] = useState<string | null>(
    searchParams.get("filename"),
  );
  const [selectedCycleId, setSelectedCycleId] = useState<number | null>(
    searchParams.get("cycle_id") ? Number(searchParams.get("cycle_id")) : null,
  );

  // Loaded session data
  const [sessions, setSessions] = useState<McapSessionSummary[]>([]);
  const [detailMap, setDetailMap] = useState<Record<string, McapSessionDetail>>(
    {},
  );
  const [cycles, setCycles] = useState<McapCycle[]>([]);
  const [cyclesCache, setCyclesCache] = useState<Record<string, McapCycle[]>>(
    {},
  );
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [cyclesLoading, setCyclesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedForArchive, setSelectedForArchive] = useState<Set<string>>(
    new Set(),
  );
  const [archiving, setArchiving] = useState(false);
  const [sessionsToArchive, setSessionsToArchive] = useState<string[] | null>(null);
  const [initialLoading, setInitialLoading] = useState(
    !!searchParams.get("cycle_id") && !searchParams.get("filename"),
  );
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const selectSession = useCallback(
    (filename: string) => {
      setSelectedFilename(filename);

      // Ultimate zero-flicker: if we have the list in cache, pre-select the first cycle immediately
      const cached = cyclesCache[filename];
      if (cached && cached.length > 0) {
        // If NO cycle_id in URL, use first cached one
        const urlCycleId = searchParams.get("cycle_id");
        if (
          urlCycleId &&
          cached.some((c) => c.cycle_id === Number(urlCycleId))
        ) {
          setSelectedCycleId(Number(urlCycleId));
        } else {
          setSelectedCycleId(cached[0].cycle_id);
        }
      } else {
        setSelectedCycleId(null);
      }

      const url = new URL(window.location.href);
      url.searchParams.set("filename", filename);
      // ONLY delete cycle_id if we are manually switching to a session that didn't have one
      // If we're entering via URL, keep it!
      if (!searchParams.get("cycle_id")) {
        url.searchParams.delete("cycle_id");
      }
      router.replace(url.pathname + url.search, { scroll: false });
    },
    [router, cyclesCache, searchParams],
  );

  const selectCycle = useCallback(
    (cycleId: number) => {
      setSelectedCycleId(cycleId);
      const url = new URL(window.location.href);
      url.searchParams.set("cycle_id", String(cycleId));
      router.replace(url.pathname + url.search, { scroll: false });
    },
    [router],
  );

  // Load MCAP sessions.
  const loadSessions = useCallback(
    async (opts?: { showSpinner?: boolean }) => {
      if (opts?.showSpinner) setSessionsLoading(true);
      try {
        const data = await api.listMcapSessions();
        const list = data?.sessions ?? [];
        setSessions(list);
        setSelectedForArchive(
          (prev) =>
            new Set([...prev].filter((name) => list.some((s) => s.filename === name))),
        );

        setSelectedFilename((prev) => {
          if (prev) return prev;
          const urlFile = searchParams.get("filename");
          return list.length > 0 ? (urlFile ?? list[0].filename) : null;
        });

        setDetailMap((prev) => {
          const toFetch = list.filter((s) => !prev[s.filename]);
          if (toFetch.length === 0) return prev;

          // Each getMcapSession() triggers a full MCAP parse on the backend
          // (first access; SQLite-cached after). Firing one per session at
          // once stampedes the parser and starves the request the user
          // actually waits on — the selected session's cycles/inspector/
          // cameras. So fetch the selected session first, then trickle the
          // rest with low concurrency.
          const selected =
            selectedFilename ?? searchParams.get("filename") ?? list[0]?.filename;
          const ordered = [...toFetch].sort((a, b) => {
            if (a.filename === selected) return -1;
            if (b.filename === selected) return 1;
            return 0;
          });

          const applyResult = (filename: string, detail: McapSessionDetail) => {
            setDetailMap((curr) =>
              curr[filename] ? curr : { ...curr, [filename]: detail },
            );
          };

          const CONCURRENCY = 2;
          let cursor = 0;
          const worker = async (): Promise<void> => {
            while (cursor < ordered.length) {
              const s = ordered[cursor++];
              try {
                const detail = await api.getMcapSession(s.filename);
                if (detail?.stats) applyResult(s.filename, detail);
              } catch {
                /* leave card badge-less; non-critical */
              }
            }
          };
          void Promise.allSettled(
            Array.from({ length: Math.min(CONCURRENCY, ordered.length) }, worker),
          );

          return { ...prev };
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes("not configured") || msg.includes("503")) {
          // Silently retry if backend service is still spinning up, DO NOT clear the loading spinner!
          setTimeout(() => loadSessions(opts), 1000);
          return;
        }
        setError(msg || "Failed to load sessions");
      }
      if (opts?.showSpinner) setSessionsLoading(false);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [searchParams],
  );

  const handleArchived = useCallback(
    (filenames: string[]) => {
      const removed = new Set(filenames);
      setSessions((prev) => prev.filter((s) => !removed.has(s.filename)));
      setDetailMap((prev) => {
        const next = { ...prev };
        filenames.forEach((name) => delete next[name]);
        return next;
      });
      setCyclesCache((prev) => {
        const next = { ...prev };
        filenames.forEach((name) => delete next[name]);
        return next;
      });
      setSelectedForArchive((prev) => {
        const next = new Set(prev);
        filenames.forEach((name) => next.delete(name));
        return next;
      });

      if (selectedFilename && removed.has(selectedFilename)) {
        setSelectedFilename(null);
        setSelectedCycleId(null);
        setCycles([]);
        router.replace("/mcap-viewer", { scroll: false });
      }
      loadSessions();
    },
    [loadSessions, router, selectedFilename],
  );

  const archiveSelectedSessions = useCallback(() => {
    const filenames = [...selectedForArchive];
    if (filenames.length === 0) return;
    setSessionsToArchive(filenames);
  }, [selectedForArchive]);

  const executeArchive = useCallback(async () => {
    if (!sessionsToArchive || sessionsToArchive.length === 0) return;
    const filenames = sessionsToArchive;
    setSessionsToArchive(null);

    setArchiving(true);
    setError(null);
    try {
      const result = await api.archiveMcapSessions(filenames);
      if (result.failed.length > 0) {
        throw new Error(`Failed to archive: ${result.failed.join(", ")}`);
      }
      handleArchived(filenames);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to archive sessions");
    } finally {
      setArchiving(false);
    }
  }, [handleArchived, sessionsToArchive]);

  useEffect(() => {
    loadSessions({ showSpinner: true });
  }, [loadSessions]);

  // Refresh on focus / system updates.
  useEffect(() => {
    const onFocus = () => {
      loadSessions();
    };
    const onUpdate = () => {
      loadSessions();
    };
    document.addEventListener("visibilitychange", onFocus);
    globalThis.addEventListener("dam-system-update", onUpdate);
    return () => {
      document.removeEventListener("visibilitychange", onFocus);
      globalThis.removeEventListener("dam-system-update", onUpdate);
    };
  }, [loadSessions]);

  // Navigate via cycle_id in URL.
  useEffect(() => {
    const urlCycleId = searchParams.get("cycle_id");
    const urlFile = searchParams.get("filename");
    if (!urlCycleId || urlFile) {
      setInitialLoading(false);
      return;
    }

    const id = Number(urlCycleId);
    api
      .findMcapSession(id)
      .then((res) => {
        if (res.found && res.filename) {
          selectSession(res.filename);
          setSelectedCycleId(id);
        }
      })
      .catch(() => {})
      .finally(() => {
        setInitialLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load cycles when selected session changes.
  useEffect(() => {
    if (!selectedFilename) return;
    let cancelled = false;
    const existing = cyclesCache[selectedFilename] || [];
    const lastId = existing.at(-1)?.cycle_id;

    if (!lastId) setCyclesLoading(true);

    api
      .listMcapCycles(selectedFilename, lastId)
      .then((data) => {
        if (cancelled) return;
        const newCycles = data?.cycles ?? [];

        // Merge with existing cache
        const updatedList = lastId ? [...existing, ...newCycles] : newCycles;
        setCycles(updatedList);
        if (newCycles.length > 0 || !lastId) {
          setCyclesCache((prev) => ({
            ...prev,
            [selectedFilename]: updatedList,
          }));
        }

        const urlCycleId = searchParams.get("cycle_id");
        let targetId: number | null = null;

        if (urlCycleId) {
          const id = Number(urlCycleId);
          if (updatedList.some((c) => c.cycle_id === id)) {
            targetId = id;
          }
        }

        if (targetId === null && updatedList.length > 0 && !selectedCycleId) {
          targetId = updatedList[0].cycle_id;
        }

        if (targetId !== null) {
          setSelectedCycleId(targetId);
        }
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load cycles");
      })
      .finally(() => {
        if (!cancelled) setCyclesLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFilename]);

  const selectedDetail = selectedFilename
    ? (detailMap[selectedFilename] ?? null)
    : null;
  const cameras = selectedDetail?.stats.cameras ?? [];
  const selectedCycle = cycles.find((c) => c.cycle_id === selectedCycleId);

  if (!isMounted) return null;

  // ── LOADING STATE ──────────────────────────────────────────────────────
  if (initialLoading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 text-dam-muted py-20 animate-in fade-in duration-500">
        <Loader2 size={32} className="animate-spin text-dam-blue" />
        <div className="text-center">
          <p className="text-sm font-bold text-dam-text uppercase tracking-widest">
            Locating Session
          </p>
          <p className="text-[10px] opacity-60 mt-1">
            Retrieving MCAP records for Cycle {searchParams.get("cycle_id")}...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-[calc(100vh-120px)] gap-5 animate-in fade-in slide-in-from-bottom-2 duration-500">
      <div className="flex gap-5 flex-1">
        {/* Left: session list */}
        <div className="w-64 shrink-0 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em]">
              Sessions ({sessions.length})
            </p>
            <button
              type="button"
              onClick={archiveSelectedSessions}
              disabled={selectedForArchive.size === 0 || archiving}
              className="inline-flex items-center gap-1 rounded border border-dam-border px-1.5 py-0.5 text-[10px] text-dam-muted hover:text-dam-text hover:border-dam-blue/40 disabled:opacity-40 disabled:cursor-not-allowed"
              title="Move selected sessions to _trash"
            >
              {archiving ? (
                <Loader2 size={10} className="animate-spin" />
              ) : (
                <Archive size={10} />
              )}
              Move
            </button>
          </div>
          <label className="flex items-center gap-2 px-1 text-[10px] text-dam-muted">
            <input
              type="checkbox"
              checked={
                sessions.length > 0 && selectedForArchive.size === sessions.length
              }
              disabled={sessions.length === 0}
              onChange={(e) =>
                setSelectedForArchive(
                  e.target.checked
                    ? new Set(sessions.map((s) => s.filename))
                    : new Set(),
                )
              }
              className="h-3.5 w-3.5 accent-dam-muted disabled:opacity-40"
            />
            Select all
          </label>

          {sessionsLoading ? (
            <div className="flex items-center gap-2 text-dam-muted py-8 justify-center rounded-lg border border-dam-border/40 bg-dam-surface-2/50">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">Loading…</span>
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex min-h-[220px] flex-col items-center justify-center rounded-lg border border-dashed border-dam-border/70 bg-dam-surface-2/50 px-4 py-8 text-center text-dam-muted space-y-2">
              <FileText size={28} className="mx-auto opacity-30" />
              <p className="text-sm">No sessions recorded yet</p>
              <p className="text-[10px] opacity-60">
                Start a run to create a session
              </p>
            </div>
          ) : (
            <div
              className="space-y-1.5 overflow-y-auto thin-scrollbar"
              style={{ maxHeight: "calc(100vh - 160px)" }}
            >
              {sessions.map((s) => (
                <SessionCard
                  key={s.filename}
                  session={s}
                  detail={detailMap[s.filename] ?? null}
                  selected={selectedFilename === s.filename}
                  checked={selectedForArchive.has(s.filename)}
                  onClick={() => selectSession(s.filename)}
                  onToggleSelect={(checked) =>
                    setSelectedForArchive((prev) => {
                      const next = new Set(prev);
                      if (checked) next.add(s.filename);
                      else next.delete(s.filename);
                      return next;
                    })
                  }
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: session detail */}
        <div className="flex-1 min-w-0 flex flex-col gap-4">
          {!selectedFilename ? (
            <>
              <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0 text-dam-muted">
                    <Film size={14} className="shrink-0 opacity-50" />
                    <span className="font-mono text-sm font-bold truncate">
                      No session selected
                    </span>
                    <span className="text-xs shrink-0">0.0 MB</span>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-dam-muted bg-dam-surface-1 px-2 py-1 rounded border border-dam-border">
                      0 cycles
                    </span>
                    <span className="text-xs text-dam-muted bg-dam-surface-1 px-2 py-1 rounded border border-dam-border">
                      0 violations
                    </span>
                    <span className="text-xs text-dam-muted bg-dam-surface-1 px-2 py-1 rounded border border-dam-border">
                      0 clamps
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled
                      className="text-[10px] text-dam-muted border border-dam-border px-2 py-1 rounded disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Metadata
                    </button>
                    <button
                      type="button"
                      disabled
                      className="flex items-center gap-1.5 text-[10px] font-bold text-dam-blue bg-dam-blue/10 border border-dam-blue/30 px-2.5 py-1 rounded disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Download size={11} />
                      Download
                    </button>
                    <button
                      type="button"
                      disabled
                      className="flex items-center justify-center p-1 text-dam-muted border border-transparent rounded disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Move to _trash"
                    >
                      <Archive size={12} />
                    </button>
                  </div>
                </div>
              </div>

              <div className="bg-dam-surface-1/50 rounded-lg p-3 min-h-[60px] relative border border-dam-border/30">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em]">
                    Cycle Timeline
                  </p>
                </div>
                <div className="flex h-10 items-center justify-center rounded border border-dashed border-dam-border/50 text-[10px] text-dam-muted/70">
                  No cycles recorded
                </div>
              </div>

              <div className="flex min-h-[360px] max-h-[calc(100vh-330px)] gap-4 overflow-hidden">
                <div className="flex flex-col flex-1 min-w-0 min-h-0">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                    Cycle Inspector
                  </p>
                  <div className="flex-1 min-h-0 rounded-lg border border-dam-border/70 bg-dam-surface-1/40 flex items-center justify-center text-center text-dam-muted">
                    <div className="space-y-2 px-6">
                      <Activity size={28} className="mx-auto opacity-30" />
                      <p className="text-sm">Select a session to view details</p>
                      <p className="text-[10px] opacity-60">
                        Recorded cycles appear here
                      </p>
                    </div>
                  </div>
                </div>

                <div className="flex flex-col w-80 shrink-0 min-h-0">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                    Camera Footage
                  </p>
                  <div className="flex-1 min-h-0 rounded-lg border border-dam-border/70 bg-dam-surface-1/40 flex items-center justify-center text-center text-dam-muted">
                    <div className="space-y-2 px-6">
                      <Film size={28} className="mx-auto opacity-30" />
                      <p className="text-sm">No camera footage</p>
                      <p className="text-[10px] opacity-60">
                        Session frames appear here
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <>
              {selectedDetail ? (
                <SessionHeader
                  session={
                    sessions.find((s) => s.filename === selectedFilename)!
                  }
                  detail={selectedDetail}
                  onArchived={handleArchived}
                />
              ) : (
                <div className="bg-dam-surface-2 border border-dam-border rounded-lg p-4 flex items-center gap-2 text-dam-muted text-sm">
                  <Loader2 size={14} className="animate-spin" />
                  Loading session info…
                </div>
              )}

              <div className="bg-dam-surface-1/50 rounded-lg p-3 min-h-[60px] relative border border-dam-border/30">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em]">
                    Cycle Timeline
                  </p>
                  {cyclesLoading && (
                    <Loader2 size={10} className="animate-spin text-dam-blue" />
                  )}
                </div>
                <McapTimelineView
                  key={selectedFilename}
                  cycles={cycles}
                  selectedCycleId={selectedCycleId ?? undefined}
                  onSelectCycle={selectCycle}
                />
              </div>

              <div className="flex min-h-[360px] max-h-[calc(100vh-330px)] gap-4 overflow-hidden">
                <div className="flex flex-col flex-1 min-w-0 min-h-0">
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                    Cycle Inspector
                  </p>
                  <div className="flex-1 min-h-0">
                    <McapCycleInspector
                      filename={selectedFilename}
                      cycleId={selectedCycleId}
                    />
                  </div>
                </div>

                <div
                  className={`flex flex-col min-h-0 ${cameras.length > 1 ? "w-[560px]" : "w-80"} shrink-0`}
                >
                  <p className="text-[9px] font-bold text-dam-muted/50 uppercase tracking-[0.15em] mb-2 px-1">
                    Camera Footage
                  </p>
                  <div className="flex-1 min-h-0">
                    <McapCameraPlayer
                      filename={selectedFilename}
                      cameras={cameras}
                      currentTimestampNs={selectedCycle?.timestamp_ns ?? null}
                    />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="fixed bottom-4 right-4 max-w-sm p-3 bg-red-500/10 border border-red-500/30 rounded-lg flex items-start gap-2 shadow-lg z-50">
          <AlertCircle size={14} className="text-red-400 shrink-0 mt-0.5" />
          <p className="text-xs text-red-400">{error}</p>
          <button
            onClick={() => setError(null)}
            className="ml-auto text-red-400/60 hover:text-red-400 text-xs"
          >
            ✕
          </button>
        </div>
      )}

      {sessionsToArchive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            aria-label="Close"
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
            onClick={() => setSessionsToArchive(null)}
          />
          <div className="relative bg-dam-surface border border-dam-border rounded-2xl w-full max-w-md shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-2.5 p-4 border-b border-dam-border bg-dam-surface-2/50">
              <AlertTriangle size={18} className="text-red-500 shrink-0" />
              <h3 className="text-sm font-bold text-dam-text uppercase tracking-widest">Delete Session</h3>
            </div>

            <div className="p-5 text-xs text-dam-muted space-y-2 leading-relaxed">
              <p>
                Are you sure you want to permanently delete{' '}
                <span className="font-mono text-dam-text font-bold bg-dam-surface-3 px-1.5 py-0.5 rounded border border-dam-border">
                  {sessionsToArchive.length === 1 ? sessionsToArchive[0] : `${sessionsToArchive.length} sessions`}
                </span>?
              </p>
              <p className="opacity-80">
                This action is permanent and cannot be undone. All recorded cycle metrics and video frames will be deleted from disk.
              </p>
            </div>

            <div className="p-4 border-t border-dam-border bg-dam-surface-2/30 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setSessionsToArchive(null)}
                className="px-4 py-2 bg-dam-surface-2 hover:bg-dam-surface-3 border border-dam-border text-dam-text text-xs font-bold rounded-lg transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={executeArchive}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-bold rounded-lg transition-colors shadow-lg shadow-red-600/10 flex items-center gap-1.5 cursor-pointer"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function McapViewerPage() {
  return (
    <PageShell
      title="MCAP Sessions"
      subtitle="Review control cycles, incidents & camera footage"
    >
      <Suspense
        fallback={
          <div className="flex items-center justify-center h-64 text-dam-muted gap-2">
            <Loader2 size={20} className="animate-spin" />
            <span>Loading…</span>
          </div>
        }
      >
        <McapViewerContent />
      </Suspense>
    </PageShell>
  );
}
