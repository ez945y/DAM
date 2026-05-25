"use client";
import React, { useState, useEffect, useLayoutEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { CycleSafetyInspector } from "@/components/CycleSafetyInspector";
import { RiskBadge } from "@/components/RiskBadge";
import type {
  PerfSnapshot,
  GuardStatus,
  RiskEvent,
  RiskLevel,
  RiskLogStats,
  FailureType,
} from "@/lib/types";
import {
  Download,
  ChevronRight,
  ChevronDown,
  Activity,
  Shield,
  Hash,
  Clock,
  Play,
  Pause,
  Trash2,
} from "lucide-react";

// ── Perf breakdown widget (pipeline + guard layers only) ──────────────────

const STAGE_COLORS: Record<string, string> = {
  source: "#6366F1",
  policy: "#F59E0B",
  guards: "#10B981",
  sink: "#3B82F6",
};
const STAGE_LABELS: Record<string, string> = {
  source: "Source",
  policy: "Policy",
  guards: "Guards",
  sink: "Sink",
};
const STAGE_ORDER = ["source", "policy", "guards", "sink"] as const;

const LAYER_META: Record<string, { label: string; color: string }> = {
  L0: { label: "L0 OOD", color: "#A78BFA" },
  L1: { label: "L1 Preflight", color: "#34D399" },
  L2: { label: "L2 Motion", color: "#10B981" },
  L3: { label: "L3 Execution", color: "#6EE7B7" },
};

/** Human-readable labels for raw latency_ms keys emitted by the runtime. */
const LATENCY_KEY_LABELS: Record<string, string> = {
  obs: "Source (Read)",
  policy: "Policy (Predict)",
  validate: "Guards (Safety Checks)",
  sink: "Sink (Dispatch)",
  total: "Total",
  source: "Source (Read)",
  guards: "Guards (Safety Checks)",
};

function PerfDetail({
  perf,
  totalMs,
}: {
  perf: PerfSnapshot;
  totalMs: number;
}) {
  const maxStage = Math.max(
    ...STAGE_ORDER.map((s) => perf.stages[s] ?? 0),
    0.001,
  );
  const layerKeys = Object.keys(perf.layers ?? {}).sort((a, b) =>
    a.localeCompare(b),
  );

  return (
    <div className="space-y-3">
      {/* Pipeline stages */}
      <div className="space-y-1.5">
        <p className="text-[9px] font-bold text-dam-orange uppercase tracking-widest">
          Pipeline Stages
        </p>
        {STAGE_ORDER.map((s) => {
          const ms = perf.stages[s] ?? 0;
          const pct = totalMs > 0 ? (ms / totalMs) * 100 : 0;
          const barW =
            totalMs > 0 ? (ms / Math.max(totalMs, maxStage)) * 100 : 0;
          return (
            <div key={s} className="flex items-center gap-2">
              <span className="w-14 text-[10px] text-dam-muted shrink-0">
                {STAGE_LABELS[s]}
              </span>
              <div className="flex-1 h-2 bg-dam-surface-1 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${barW}%`, background: STAGE_COLORS[s] }}
                />
              </div>
              <span className="w-16 text-right font-mono text-[10px] text-dam-text">
                {ms.toFixed(1)} ms
              </span>
              <span className="w-8 text-right font-mono text-[9px] text-dam-muted">
                {pct.toFixed(0)}%
              </span>
            </div>
          );
        })}
        <div className="flex items-center gap-2 border-t border-dam-border/30 pt-1 mt-1">
          <span className="w-14 text-[10px] font-bold text-dam-muted shrink-0">
            Total
          </span>
          <div className="flex-1" />
          <span className="w-16 text-right font-mono text-[10px] font-bold text-dam-orange">
            {totalMs.toFixed(1)} ms
          </span>
          <span className="w-8" />
        </div>
      </div>

      {/* Guard layers */}
      {layerKeys.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[9px] font-bold text-dam-orange uppercase tracking-widest">
            Guard Layers
          </p>
          {layerKeys.map((k) => {
            const ms = perf.layers[k] ?? 0;
            const barW = totalMs > 0 ? (ms / totalMs) * 100 : 0;
            const meta = LAYER_META[k];
            return (
              <div key={k} className="flex items-center gap-2">
                <span className="w-14 text-[10px] text-dam-muted shrink-0">
                  {meta?.label ?? k}
                </span>
                <div className="flex-1 h-2 bg-dam-surface-1 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${barW}%`,
                      background: meta?.color ?? "#6B6B6B",
                    }}
                  />
                </div>
                <span className="w-16 text-right font-mono text-[10px] text-dam-text">
                  {ms.toFixed(1)} ms
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Expandable guard result item ──────────────────────────────────────────

function GuardResultItem({
  g,
  guardLatencyMs,
}: {
  g: GuardStatus;
  guardLatencyMs?: number;
}) {
  const [open, setOpen] = useState(false);
  const decColor =
    g.decision === "PASS"
      ? "text-dam-green"
      : g.decision === "CLAMP"
        ? "text-dam-blue"
        : g.decision === "FAULT"
          ? "text-dam-red"
          : "text-dam-orange";

  return (
    <div className="group/item bg-dam-surface-3 border border-dam-border rounded hover:border-dam-blue/30 transition-colors overflow-hidden">
      {/* Header row — always visible, click to expand */}
      <button
        type="button"
        className="flex items-center gap-2 p-2 cursor-pointer select-none w-full text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-dam-muted/50 shrink-0">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
        <span className="text-[10px] bg-dam-surface-1 px-1 rounded border border-dam-border text-dam-muted shrink-0">
          {g.layer}
        </span>
        <span className="text-xs font-bold text-dam-text font-mono flex-1 truncate">
          {g.name}
        </span>
        {guardLatencyMs !== undefined && (
          <span className="text-[9px] font-mono text-dam-muted shrink-0 ml-1">
            {guardLatencyMs.toFixed(1)} ms
          </span>
        )}
        <span
          className={`text-[10px] font-bold px-1.5 rounded-sm shrink-0 ${decColor}`}
        >
          {g.decision}
        </span>
      </button>

      {/* Expanded: full reason + metadata + latency detail */}
      {open && (
        <div className="border-t border-dam-border/40 px-3 pb-2 pt-1.5 space-y-1.5 bg-black/20">
          {g.reason ? (
            <p className="text-[11px] text-dam-muted font-mono leading-relaxed whitespace-pre-wrap">
              {g.reason}
            </p>
          ) : !g.metadata || Object.keys(g.metadata).length === 0 ? (
            <p className="text-[11px] text-dam-muted/50 italic">
              Safety check passed — no telemetry reported.
            </p>
          ) : null}
          {g.metadata && Object.keys(g.metadata).length > 0 && (
            <GuardMetadataView metadata={g.metadata} />
          )}
          {guardLatencyMs !== undefined && (
            <p className="text-[10px] text-dam-muted/60 font-mono pt-1 border-t border-dam-border/20">
              Guard execution time:{" "}
              <span className="text-dam-text">
                {guardLatencyMs.toFixed(2)} ms
              </span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Guard metadata renderer (sensor telemetry for L3 hardware guard) ──────

function GuardMetadataView({
  metadata,
}: {
  metadata: Record<string, unknown>;
}) {
  const exceptionJoints = new Set(
    Array.isArray(metadata.exception_joints)
      ? (metadata.exception_joints as number[])
      : [],
  );
  const sections: {
    key: string;
    label: string;
    unit: string;
    format: (v: number) => string;
  }[] = [
    {
      key: "temperatures",
      label: "Temperature",
      unit: "°C",
      format: (v) => v.toFixed(0) + "°",
    },
    {
      key: "currents",
      label: "Current",
      unit: "A",
      format: (v) => v.toFixed(2) + "A",
    },
    {
      key: "voltages",
      label: "Voltage",
      unit: "V",
      format: (v) => v.toFixed(1) + "V",
    },
  ];
  const rows = sections
    .map((s) => {
      const dict = metadata[s.key];
      if (!dict || typeof dict !== "object") return null;
      const entries = Object.entries(dict as Record<string, number>);
      if (entries.length === 0) return null;
      return { ...s, entries };
    })
    .filter(Boolean) as {
    key: string;
    label: string;
    entries: [string, number][];
    format: (v: number) => string;
  }[];

  if (rows.length === 0) return null;

  return (
    <div className="space-y-1.5 font-mono">
      {rows.map((r) => (
        <div key={r.key} className="text-[10px]">
          <div className="text-dam-muted/70 uppercase tracking-wider mb-0.5">
            {r.label}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {r.entries.map(([joint, val]) => {
              const idx = Number(joint.replace(/^J/, ""));
              const skipped = exceptionJoints.has(idx);
              return (
                <span
                  key={joint}
                  className={`px-1.5 py-0.5 rounded border text-[10px] ${
                    skipped
                      ? "bg-dam-surface-1 border-dam-border/40 text-dam-muted/60 line-through"
                      : "bg-dam-surface-1 border-dam-border text-dam-text"
                  }`}
                  title={
                    skipped ? `${joint} skipped by exception_joints` : joint
                  }
                >
                  <span className="text-dam-muted/60">{joint}</span>
                  <span className="ml-1">{r.format(val)}</span>
                </span>
              );
            })}
          </div>
        </div>
      ))}
      {exceptionJoints.size > 0 && (
        <p className="text-[10px] text-dam-muted/60 italic pt-0.5">
          Skipped joints:{" "}
          {[...exceptionJoints]
            .sort((a, b) => a - b)
            .map((i) => `J${i}`)
            .join(", ")}
        </p>
      )}
    </div>
  );
}
// ── Full per-event detail panel (reused for single events and inside groups) ──

function EventDetailPanel({ e }: { e: RiskEvent }) {
  const guards = (e.guard_results ?? []).map((g) => ({
    name: g.name,
    layer: g.layer,
    decision: g.decision,
    reason: g.reason,
    latency_ms: e.perf?.guards?.[g.name],
    metadata: g.metadata,
  }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4 text-[10px] text-dam-muted uppercase tracking-widest border-b border-dam-border/40 pb-2">
        <div className="flex items-center gap-1.5">
          <Hash size={12} /> Trace:{" "}
          <span className="text-dam-text font-mono select-all uppercase">
            {e.trace_id}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock size={12} /> Full Time:{" "}
          <span className="text-dam-text font-mono">
            {new Date(e.timestamp * 1000).toISOString()}
          </span>
        </div>
      </div>

      <div className="h-[420px]">
        <CycleSafetyInspector
          mode="risk"
          guards={guards}
          latency={e.perf?.stages ?? e.latency_ms}
          totalMs={e.latency_ms["total"] ?? e.perf?.stages?.["total"] ?? 0}
          failure={{
            type: e.failure_type,
            guardNames: e.failure_guard_names,
            layers: e.failure_layers,
            decisions: e.failure_decisions,
            reasons: e.failure_reasons,
            tuple: e.failure_tuple,
          }}
          observation={e.observation}
          action={e.action}
          hardware={e.hardware}
        />
      </div>

      <ViewMcapButton cycleId={e.cycle_id} mcapFilename={e.mcap_filename} />
    </div>
  );
}

// ── Per-event mini-row content (richer info for inner list inside a group) ──

function fmtTimeMs(ts: number): string {
  const d = new Date(ts * 1000);
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${d.toLocaleTimeString()}.${ms}`;
}

function pickNumericDict(
  meta: Record<string, unknown> | undefined,
  key: string,
): [string, number][] | null {
  const v = meta?.[key];
  if (!v || typeof v !== "object") return null;
  const entries = Object.entries(v as Record<string, number>);
  return entries.length > 0 ? entries : null;
}

function sensorGlimpse(events: RiskEvent[]): {
  tMax?: [string, number];
  iMax?: [string, number];
} {
  // Latest event with adapter telemetry; old guard-metadata records remain readable.
  const meta = events.find((e) => e.hardware?.temperatures || e.hardware?.currents || e.guard_results?.some(
    (g) => g.metadata && (g.metadata.temperatures || g.metadata.currents),
  ));
  if (!meta) return {};
  const hw = meta.hardware ?? meta.guard_results.find(
    (g) => g.metadata?.temperatures || g.metadata?.currents,
  )?.metadata;
  const temps = pickNumericDict(hw, "temperatures");
  const currs = pickNumericDict(hw, "currents");
  const tMax = temps?.reduce<[string, number]>(
    (acc, e) => (e[1] > acc[1] ? e : acc),
    temps[0],
  );
  const iMax = currs?.reduce<[string, number]>(
    (acc, e) => (e[1] > acc[1] ? e : acc),
    currs[0],
  );
  return { tMax, iMax };
}

// One chip per LAYER, colored by the worst decision in that layer.
//   green  = all PASS
//   blue   = at least one CLAMP (and no REJECT/FAULT)
//   red    = at least one REJECT or FAULT
const _DECISION_RANK: Record<string, number> = {
  PASS: 0,
  CLAMP: 1,
  REJECT: 2,
  FAULT: 3,
};

function GuardChips({ results }: { results: GuardStatus[] }) {
  const byLayer = new Map<string, { worst: string; reasons: string[] }>();
  for (const g of results) {
    const slot = byLayer.get(g.layer);
    if (!slot) {
      byLayer.set(g.layer, {
        worst: g.decision,
        reasons: g.reason ? [`${g.name}: ${g.reason}`] : [],
      });
      continue;
    }
    if ((_DECISION_RANK[g.decision] ?? 0) > (_DECISION_RANK[slot.worst] ?? 0)) {
      slot.worst = g.decision;
    }
    if (g.reason) slot.reasons.push(`${g.name}: ${g.reason}`);
  }
  const layers = [...byLayer.entries()].sort((a, b) =>
    a[0].localeCompare(b[0]),
  );

  const colorFor = (d: string) =>
    d === "PASS"
      ? "bg-emerald-950/40 text-dam-green border-emerald-900/40"
      : d === "CLAMP"
        ? "bg-blue-950/40 text-dam-blue border-blue-900/40"
        : /* REJECT, FAULT */ "bg-red-950/40 text-dam-red border-red-900/40";

  return (
    <div className="flex flex-wrap gap-1">
      {layers.map(([layer, info]) => (
        <span
          key={layer}
          className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold border ${colorFor(info.worst)}`}
          title={
            info.reasons.length > 0
              ? info.reasons.join("\n")
              : `${layer} ${info.worst}`
          }
        >
          {layer}
        </span>
      ))}
    </div>
  );
}

// ── View MCAP Button ─────────────────────────────────────────────────────

function ViewMcapButton({
  cycleId,
  mcapFilename,
}: {
  cycleId: number;
  mcapFilename?: string | null;
}) {
  const router = useRouter();

  return (
    <button
      onClick={() => {
        const url = mcapFilename
          ? `/mcap-viewer?filename=${encodeURIComponent(mcapFilename)}&cycle_id=${cycleId}`
          : `/mcap-viewer?cycle_id=${cycleId}`;
        router.push(url);
      }}
      className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 bg-dam-blue/10 border border-dam-blue/30 text-dam-blue text-xs font-bold rounded hover:bg-dam-blue/20 transition-colors"
    >
      <Play size={12} /> View in MCAP Viewer
    </button>
  );
}

// ── Failure-category badge (DAM event taxonomy) ───────────────────────────
// Mirrors dam.runtime.failure_classify — three high-level categories that
// make incident triage fast without reading individual guard rows.
const FAILURE_CAT: Record<
  string,
  { label: string; cls: string; tip: string }
> = {
  ood_only: {
    label: "Perception",
    cls: "text-violet-300 bg-violet-500/10 border-violet-500/30",
    tip: "感知異常事件 — 僅有 L0 感知異常安全防護觸發",
  },
  guard_triggered: {
    label: "Action",
    cls: "text-amber-300 bg-amber-500/10 border-amber-500/30",
    tip: "動作風險事件 — 任一非硬體安全防護對動作進行拒絕、限制或故障裁決",
  },
  hardware_triggered: {
    label: "Hardware",
    cls: "text-red-300 bg-red-500/10 border-red-500/30",
    tip: "硬體風險事件 — 任一 L3 安全防護或硬體錯誤來源觸發",
  },
};

function FailureTypeBadge({ type }: { type?: string | null }) {
  const cat = type ? FAILURE_CAT[type] : undefined;
  if (!cat) return <span className="text-dam-muted">—</span>;
  return (
    <span
      title={cat.tip}
      className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${cat.cls}`}
    >
      {cat.label}
    </span>
  );
}

// ── Main table component ──────────────────────────────────────────────────

export function RiskLogTable() {
  const [isMounted, setIsMounted] = useState(false);
  const searchParams = useSearchParams();
  const targetCycleId = searchParams
    ? Number(searchParams.get("cycle_id") ?? "") || null
    : null;

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [_autoRefresh, setAutoRefresh] = useState(true);
  // Deep-link from the dashboard (?cycle_id=N) freezes the log so live updates
  // don't shift the row of interest out from under the user.
  // Pure useState + useEffect races the [filters] effect that attaches the
  // dam-risk-update listener — the listener can attach with frozenRef still
  // false, letting one or two batches of events slip through before the
  // catch-up effect sets the ref. Mirroring `frozen` into a ref via
  // useLayoutEffect closes that gap (layout-effects run synchronously after
  // commit, before any useEffect).
  const [frozen, setFrozen] = useState(false);
  const [filters, setFilters] = useState({
    min_risk_level: "",
    outcome: "" as "" | "reject" | "clamp",
    failure_type: "" as "" | FailureType,
    limit: 1000,
  });
  const [search, setSearch] = useState("");
  const [grouping, setGrouping] = useState(true);
  // Track expansion by event_id "anchors" rather than positional group keys.
  // A group is considered expanded if it still contains any of its anchor ids.
  // We anchor on the newest event at click time — that one is the last to be
  // evicted by the rolling buffer, so expansion survives reorders and growth.
  const [expandedAnchorIds, setExpandedAnchorIds] = useState<Set<number>>(
    new Set(),
  );
  const [expandedEventIds, setExpandedEventIds] = useState<Set<number>>(
    new Set(),
  );
  const highlightRef = useRef<HTMLTableRowElement | null>(null);
  const didAutoExpand = useRef(false);

  const toggleGroupExpand = (group: { events: RiskEvent[] }) => {
    setExpandedAnchorIds((prev) => {
      const next = new Set(prev);
      const alreadyOpen = group.events.some((e) => next.has(e.event_id));
      if (alreadyOpen) {
        for (const e of group.events) next.delete(e.event_id);
      } else {
        // Anchor on the newest event in the group (events[0]).
        next.add(group.events[0].event_id);
      }
      return next;
    });
  };

  const toggleEventExpand = (eventId: number) => {
    setExpandedEventIds((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  };

  const load = async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const evRes = await api.getRiskLog({
        min_risk_level: filters.min_risk_level || undefined,
        outcome: filters.outcome || undefined,
        failure_type: filters.failure_type || undefined,
        limit: filters.limit,
      });
      setEvents(evRes.events);
    } catch {
      /* ignore */
    } finally {
      if (!isBackground) setLoading(false);
    }
  };

  // Listen for global live refresh toggle
  useEffect(() => {
    const sync = () => {
      const saved = localStorage.getItem("dam_live_refresh");
      setAutoRefresh(saved !== "false");
    };
    sync();
    globalThis.addEventListener("dam_live_refresh_change", sync);
    return () =>
      globalThis.removeEventListener("dam_live_refresh_change", sync);
  }, []);

  // Auto-refresh: Switch to event-driven instead of constant polling.
  // frozen only gates the handlers — no backend API is called; MCAP freeze is
  // managed exclusively by the MCAP viewer page.
  // useLayoutEffect, not useEffect: runs synchronously after the render
  // commit, before any useEffect — including the [filters] one below that
  // attaches the dam-risk-update listener. So by the time the listener can
  // fire, frozenRef.current is already true on a deep-link.
  const frozenRef = useRef(false);
  useLayoutEffect(() => {
    frozenRef.current = frozen;
  }, [frozen]);

  // Deep-link auto-freeze. Sets the ref *directly* (not just state) so the
  // listener-attach effect can read it on its first run. Setting state too so
  // the Freeze/Resume button shows the right label.
  useLayoutEffect(() => {
    if (targetCycleId !== null && !frozenRef.current) {
      frozenRef.current = true;
      setFrozen(true);
    }
  }, [targetCycleId]);

  useEffect(() => {
    load();

    const handleUpdate = () => {
      if (!frozenRef.current) load(true);
    };
    const handleFocus = () => {
      if (!frozenRef.current) load(true);
    };

    globalThis.addEventListener("dam-system-update", handleUpdate);
    globalThis.addEventListener("dam-risk-update", handleUpdate);
    globalThis.addEventListener("focus", handleFocus);

    return () => {
      globalThis.removeEventListener("dam-system-update", handleUpdate);
      globalThis.removeEventListener("dam-risk-update", handleUpdate);
      globalThis.removeEventListener("focus", handleFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  const handleClear = async () => {
    try {
      await api.clearRiskLog();
      setEvents([]);
      setExpandedAnchorIds(new Set());
      setExpandedEventIds(new Set());
    } catch {
      /* ignore */
    }
  };

  // Reason-text + decision search runs client-side over the already-fetched
  // page; backend filters (risk level / rejected / clamped) gate the fetch.
  const matchesSearch = (e: RiskEvent) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    if (e.fallback_triggered?.toLowerCase().includes(q)) return true;
    if (e.trace_id?.toLowerCase().includes(q)) return true;
    return (e.guard_results ?? []).some(
      (g) =>
        g.reason?.toLowerCase().includes(q) ||
        g.name?.toLowerCase().includes(q) ||
        g.decision?.toLowerCase().includes(q),
    );
  };

  const visibleEvents = events.filter(matchesSearch);

  // Group consecutive identical events; each group keeps every cycle so the
  // expanded view can render them individually (no data hidden behind ×N).
  const groupEvents = (raw: RiskEvent[]) => {
    if (raw.length === 0)
      return [] as (RiskEvent & { count: number; events: RiskEvent[] })[];
    const groups: (RiskEvent & { count: number; events: RiskEvent[] })[] = [];
    for (const ev of raw) {
      const last = groups.at(-1);
      const sameAsLast =
        last !== undefined &&
        last.risk_level === ev.risk_level &&
        last.was_rejected === ev.was_rejected &&
        last.was_clamped === ev.was_clamped &&
        last.fallback_triggered === ev.fallback_triggered;
      if (sameAsLast) {
        last.count++;
        last.events.push(ev);
      } else {
        groups.push({ ...ev, count: 1, events: [ev] });
      }
    }
    return groups;
  };

  // grouping=false means one row per event (Wireshark-style).
  const groupedEvents = grouping
    ? groupEvents(visibleEvents)
    : visibleEvents.map((e) => ({ ...e, count: 1, events: [e] }));

  // Auto-expand + scroll to target cycle when URL param is set.
  useEffect(() => {
    if (!targetCycleId || didAutoExpand.current || events.length === 0) return;
    const ev = events.find((e) => e.cycle_id === targetCycleId);
    if (!ev) return;
    setExpandedEventIds((prev) => new Set(prev).add(ev.event_id));
    // Also open the containing group row so the inner event is reachable.
    const groups = groupEvents(events);
    const g = groups.find((grp) =>
      grp.events.some((e) => e.event_id === ev.event_id),
    );
    if (g) {
      setExpandedAnchorIds((prev) => new Set(prev).add(g.events[0].event_id));
    }
    didAutoExpand.current = true;
    setTimeout(() => {
      highlightRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 150);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetCycleId, events]);

  if (!isMounted) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-3">
        <div className="w-6 h-6 border-2 border-dam-blue/40 border-t-dam-blue rounded-full animate-spin" />
        <span className="text-dam-muted text-xs">Loading Risk Log...</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Inspection bar — grouped: triage filters · search · view · actions */}
      <div className="bg-dam-surface-1 border border-dam-border rounded">
        {/* Row 1 — triage filters: narrow down WHERE the problem is */}
        <div className="flex flex-wrap items-end gap-x-5 gap-y-3 p-3">
          <div className="flex flex-col gap-1">
            <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Severity
            </span>
            <select
              value={filters.min_risk_level}
              onChange={(e) =>
                setFilters((f) => ({ ...f, min_risk_level: e.target.value }))
              }
              className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5"
            >
              <option value="">All risk levels</option>
              <option value="ELEVATED">≥ ELEVATED</option>
              <option value="CRITICAL">≥ CRITICAL</option>
              <option value="EMERGENCY">EMERGENCY only</option>
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Failure layer
            </span>
            <select
              value={filters.failure_type}
              onChange={(e) =>
                setFilters((f) => ({
                  ...f,
                  failure_type: e.target.value as "" | FailureType,
                }))
              }
              className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5"
            >
              <option value="">All failure types</option>
              <option value="ood_only">Perception · L0 only</option>
              <option value="guard_triggered">Action · non-hardware</option>
              <option value="hardware_triggered">Hardware · L3 / hw</option>
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Outcome
            </span>
            <div className="flex rounded border border-dam-border overflow-hidden">
              {([["reject", "Reject"], ["clamp", "Clamp"]] as const).map(([value, label]) => {
                const on = filters.outcome === value;
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() =>
                      setFilters((f) => ({ ...f, outcome: f.outcome === value ? "" : value }))
                    }
                    className={`px-2.5 py-1.5 text-xs font-medium transition-colors border-r border-dam-border last:border-r-0 ${
                      on
                        ? "bg-dam-blue/15 text-dam-blue"
                        : "bg-dam-surface-2 text-dam-muted hover:text-dam-text"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex flex-1 min-w-[12rem] flex-col gap-1">
            <span className="text-[9px] font-bold uppercase tracking-widest text-dam-muted/70">
              Search
            </span>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="reason · guard · trace · fallback…"
              className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5 w-full"
            />
          </div>
        </div>

        {/* Row 2 — view + actions */}
        <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-dam-border/50 bg-dam-surface-2/40">
          <button
            type="button"
            onClick={() => setGrouping((v) => !v)}
            className={`flex items-center gap-1.5 px-2 py-1.5 border text-xs rounded transition-colors ${
              grouping
                ? "bg-dam-blue/15 border-dam-blue/40 text-dam-blue"
                : "bg-dam-surface-2 border-dam-border text-dam-muted hover:text-dam-text"
            }`}
            title="Collapse consecutive identical events into one expandable row"
          >
            Group similar
          </button>

          {loading && (
            <span className="flex items-center gap-1.5 text-[10px] text-dam-muted">
              <span className="w-3 h-3 border-2 border-dam-blue/40 border-t-dam-blue rounded-full animate-spin" />
              syncing…
            </span>
          )}

          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => setFrozen((v) => !v)}
              className={`flex items-center gap-1 px-2.5 py-1.5 border text-xs font-bold rounded transition-colors ${
                frozen
                  ? "bg-dam-orange/10 border-dam-orange/40 text-dam-orange hover:bg-dam-orange/20"
                  : "bg-dam-surface-2 border-dam-border text-dam-muted hover:text-dam-text"
              }`}
              title={
                frozen
                  ? "Live updates paused — click to resume"
                  : "Pause live updates so rows don't shift while inspecting"
              }
            >
              {frozen ? (
                <>
                  <Play size={11} /> Resume
                </>
              ) : (
                <>
                  <Pause size={11} /> Freeze
                </>
              )}
            </button>

            <div className="h-5 w-px bg-dam-border/40" />

            <a
              href={api.exportRiskLogJsonUrl()}
              download="risk_log.json"
              className="flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
            >
              <Download size={11} /> JSON
            </a>
            <a
              href={api.exportRiskLogCsvUrl()}
              download="risk_log.csv"
              className="flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
            >
              <Download size={11} /> CSV
            </a>
            <button
              onClick={handleClear}
              className="flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-red hover:border-dam-red/40 transition-colors"
              title="Clear the in-memory risk log"
            >
              <Trash2 size={11} /> Clear
            </button>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto border border-dam-border rounded-lg bg-dam-surface-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-dam-surface-2 border-b border-dam-border text-dam-muted">
              <th className="w-8 px-4 py-2"></th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                Time
              </th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                ID
              </th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                Risk
              </th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                Type
              </th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                Outcome
              </th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">
                Lat
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-dam-border/40">
            {groupedEvents.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center text-dam-muted py-12">
                  No risk events recorded.
                </td>
              </tr>
            ) : (
              groupedEvents.map((e) => {
                // React key uses the newest event_id at render time — fine for
                // diffing, may change if a new event joins the front of the group
                // (the whole subtree re-keys, but state lives in expandedAnchorIds).
                const reactKey = `g-${e.events[0].event_id}`;
                const isExpanded = e.events.some((s) =>
                  expandedAnchorIds.has(s.event_id),
                );
                const isTarget =
                  targetCycleId !== null &&
                  e.events.some((s) => s.cycle_id === targetCycleId);
                const glimpse = sensorGlimpse(e.events);
                return (
                  <React.Fragment key={reactKey}>
                    <tr
                      ref={isTarget ? highlightRef : undefined}
                      onClick={() => toggleGroupExpand(e)}
                      className={`hover:bg-dam-surface-2/60 transition-colors group cursor-pointer border-l-2 ${
                        isTarget
                          ? "bg-dam-blue/5 border-dam-blue animate-pulse-once"
                          : isExpanded
                            ? "bg-dam-surface-2 border-dam-blue"
                            : "border-transparent"
                      }`}
                    >
                      <td className="px-4 py-3 text-center text-dam-muted">
                        <div className="flex items-center gap-1.5">
                          {isExpanded ? (
                            <ChevronDown size={14} />
                          ) : (
                            <ChevronRight size={14} />
                          )}
                          {e.count > 1 && (
                            <span className="text-[9px] text-dam-blue font-mono font-bold">
                              ×{e.count}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-dam-muted">
                        {new Date(e.timestamp * 1000).toLocaleTimeString()}
                      </td>
                      <td className="px-4 py-3 text-dam-muted font-mono">
                        #{e.event_id}
                      </td>
                      <td className="px-4 py-3">
                        <RiskBadge level={e.risk_level as RiskLevel} />
                      </td>
                      <td className="px-4 py-3">
                        <FailureTypeBadge type={e.failure_type} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          {e.outcome === "reject" && (
                            <span className="px-1.5 py-0.5 rounded bg-orange-950/40 text-dam-orange border border-orange-900/40 font-bold text-[10px]">
                              REJECT
                            </span>
                          )}
                          {e.outcome === "clamp" && (
                            <span className="px-1.5 py-0.5 rounded bg-blue-950/40 text-dam-blue border border-blue-900/40 font-bold text-[10px]">
                              CLAMP
                            </span>
                          )}
                          {e.fallback_triggered && (
                            <span className="px-1.5 py-0.5 rounded bg-orange-950/40 text-dam-orange border border-orange-900/40 font-bold text-[10px]">
                              FALLBACK
                            </span>
                          )}
                          {e.outcome === "pass" &&
                            !e.fallback_triggered && (
                              <span className="text-dam-muted">—</span>
                            )}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-dam-muted">
                        {e.latency_ms["total"]?.toFixed(1) ?? "—"}
                        <span className="text-[9px] opacity-60">ms</span>
                      </td>
                    </tr>

                    {isExpanded && (
                      <tr className="bg-dam-surface-2/40 animate-in slide-in-from-top-1 duration-200">
                        <td
                          colSpan={7}
                          className="px-8 py-4 border-l-2 border-dam-blue"
                        >
                          {e.count === 1 ? (
                            <EventDetailPanel e={e.events[0]} />
                          ) : (
                            <div className="space-y-1.5">
                              <div className="flex items-center justify-between text-[10px] text-dam-muted uppercase tracking-widest pb-1">
                                <span>
                                  {e.count} cycles in group — click a row for
                                  full detail
                                </span>
                                {(glimpse.tMax || glimpse.iMax) && (
                                  <span className="normal-case tracking-normal font-mono">
                                    {glimpse.tMax && (
                                      <span className="mr-3">
                                        max T:{" "}
                                        <span className="text-dam-text">
                                          {glimpse.tMax[0]}{" "}
                                          {glimpse.tMax[1].toFixed(0)}°
                                        </span>
                                      </span>
                                    )}
                                    {glimpse.iMax && (
                                      <span>
                                        max I:{" "}
                                        <span className="text-dam-text">
                                          {glimpse.iMax[0]}{" "}
                                          {glimpse.iMax[1].toFixed(2)}A
                                        </span>
                                      </span>
                                    )}
                                  </span>
                                )}
                              </div>
                              {e.events.map((sub) => {
                                const innerOpen = expandedEventIds.has(
                                  sub.event_id,
                                );
                                const subGlimpse = sensorGlimpse([sub]);
                                return (
                                  <div
                                    key={sub.event_id}
                                    className="bg-dam-surface-3 border border-dam-border rounded"
                                  >
                                    <button
                                      type="button"
                                      onClick={() =>
                                        toggleEventExpand(sub.event_id)
                                      }
                                      className="w-full flex items-center gap-2 px-2 py-1.5 text-left hover:bg-dam-surface-2 transition-colors"
                                    >
                                      <span className="text-dam-muted/50 shrink-0">
                                        {innerOpen ? (
                                          <ChevronDown size={12} />
                                        ) : (
                                          <ChevronRight size={12} />
                                        )}
                                      </span>
                                      <span className="text-[10px] text-dam-muted font-mono w-24 shrink-0">
                                        {fmtTimeMs(sub.timestamp)}
                                      </span>
                                      <span className="text-[10px] text-dam-muted font-mono w-14 shrink-0">
                                        #{sub.event_id}
                                      </span>
                                      <span className="text-[10px] text-dam-muted font-mono w-20 shrink-0">
                                        cyc {sub.cycle_id}
                                      </span>
                                      {sub.guard_results &&
                                        sub.guard_results.length > 0 && (
                                          <span className="shrink-0">
                                            <GuardChips
                                              results={sub.guard_results}
                                            />
                                          </span>
                                        )}
                                      <span className="flex-1 text-[10px] font-mono text-dam-muted truncate">
                                        {subGlimpse.tMax && (
                                          <span className="mr-2">
                                            {subGlimpse.tMax[0]}:
                                            {subGlimpse.tMax[1].toFixed(0)}°
                                          </span>
                                        )}
                                        {subGlimpse.iMax && (
                                          <span>
                                            {subGlimpse.iMax[0]}:
                                            {subGlimpse.iMax[1].toFixed(2)}A
                                          </span>
                                        )}
                                      </span>
                                      <span className="text-[10px] font-mono text-dam-muted shrink-0">
                                        {sub.latency_ms["total"]?.toFixed(1) ??
                                          "—"}{" "}
                                        ms
                                      </span>
                                    </button>
                                    {innerOpen && (
                                      <div className="border-t border-dam-border/40 px-3 py-3 bg-black/10">
                                        <EventDetailPanel e={sub} />
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
