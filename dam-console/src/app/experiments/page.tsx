"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, FileSpreadsheet, FlaskConical, Loader2, Play } from "lucide-react";
import { PageShell } from "@/components/PageShell";
import { api } from "@/lib/api";
import type { ExperimentDef, ExperimentResult } from "@/lib/types";

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseFpsValues(value: unknown): number[] {
  if (Array.isArray(value)) {
    return value.map(Number).filter((fps) => Number.isFinite(fps) && fps > 0);
  }
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return [value];
  }
  return String(value ?? "10,20,50")
    .split(",")
    .map((part) => Number(part.trim()))
    .filter((fps) => Number.isFinite(fps) && fps > 0);
}

function mergeExperimentResult(
  previous: ExperimentResult | undefined,
  next: ExperimentResult,
): ExperimentResult {
  const artifacts = Array.from(new Set([...(previous?.artifacts ?? []), ...next.artifacts]));
  return {
    ...next,
    elapsed_sec: (previous?.elapsed_sec ?? 0) + next.elapsed_sec,
    rows: [...(previous?.rows ?? []), ...next.rows],
    artifacts,
  };
}

function RowPreview({ result }: { result: ExperimentResult }) {
  const rows = result.rows;
  const columns = rows.length ? Object.keys(rows[0]).slice(0, 7) : [];
  if (!rows.length) {
    return <p className="text-xs text-dam-muted">No rows returned.</p>;
  }
  return (
    <div className="overflow-x-auto border border-dam-border rounded-lg">
      <table className="w-full text-left text-[11px]">
        <thead className="bg-dam-surface-2 text-dam-muted uppercase tracking-wider">
          <tr>
            {columns.map((col) => (
              <th key={col} className="px-2 py-2 font-semibold whitespace-nowrap">{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx} className="border-t border-dam-border/60">
              {columns.map((col) => {
                const value = row[col];
                const n = asNumber(value);
                return (
                  <td key={col} className="px-2 py-2 font-mono text-dam-text/90 whitespace-nowrap">
                    {n === null ? String(value ?? "") : n.toFixed(Math.abs(n) < 10 ? 4 : 2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function isPreviewArtifact(path: string): boolean {
  const lower = path.toLowerCase();
  return (
    lower.endsWith(".svg") ||
    lower.endsWith(".png") ||
    lower.endsWith(".jpg") ||
    lower.endsWith(".jpeg")
  );
}

function primaryPreviewArtifacts(paths: string[]): string[] {
  const images = paths.filter(isPreviewArtifact);
  const png = images.find((path) => path.toLowerCase().endsWith(".png"));
  return png ? [png] : images.slice(0, 1);
}

function visibleArtifacts(result: ExperimentResult | null): string[] {
  if (!result) return [];
  return result.id === "latency-bench" ? [] : result.artifacts;
}

const CHART_COLORS = ["#3b82f6", "#22c55e", "#f97316", "#ef4444", "#a855f7"];

function LatencyGroupedBarChart({ rows }: { rows: Record<string, unknown>[] }) {
  const fpsValues = Array.from(
    new Set(rows.map((row) => asNumber(row.target_fps)).filter((n): n is number => n !== null)),
  ).sort((a, b) => a - b);
  const configs = Array.from(
    new Set(rows.map((row) => String(row.config ?? "")).filter(Boolean)),
  );
  const values = rows
    .map((row) => asNumber(row.p95_ms))
    .filter((n): n is number => n !== null);
  if (!fpsValues.length || !configs.length || !values.length) {
    return null;
  }

  const width = 860;
  const height = 320;
  const margin = { top: 26, right: 26, bottom: 46, left: 58 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const yMax = Math.max(0.01, Math.max(...values) * 1.2);
  const y = (value: number) => margin.top + plotH - (value / yMax) * plotH;
  const ticks = [0, yMax / 2, yMax];
  const groupW = plotW / configs.length;
  const barGap = 3;
  const barW = Math.max(5, Math.min(22, (groupW * 0.72) / Math.max(fpsValues.length, 1) - barGap));
  const groupCenter = (idx: number) => margin.left + groupW * idx + groupW / 2;

  return (
    <div className="rounded-lg border border-dam-border bg-dam-surface-1 overflow-hidden">
      <div className="px-3 py-2 border-b border-dam-border">
        <p className="text-[10px] font-bold uppercase tracking-widest text-dam-muted">
          RQ4 p95 Guard Latency · Grouped by Safety Config
        </p>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto bg-dam-surface-2">
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={margin.left}
              x2={width - margin.right}
              y1={y(tick)}
              y2={y(tick)}
              stroke="rgba(255,255,255,0.08)"
            />
            <text x={margin.left - 10} y={y(tick) + 4} textAnchor="end" className="fill-dam-muted text-[10px]">
              {tick.toFixed(tick < 1 ? 3 : 1)}
            </text>
          </g>
        ))}
        {configs.map((config, idx) => (
          <g key={config}>
            <text x={groupCenter(idx)} y={height - 18} textAnchor="middle" className="fill-dam-muted text-[10px]">
              {config}
            </text>
          </g>
        ))}
        <text x={18} y={24} className="fill-dam-muted text-[10px]">
          p95 ms
        </text>
        <text x={width - margin.right} y={24} textAnchor="end" className="fill-dam-muted text-[10px]">
          budgets: {fpsValues.map((fps) => `${fps}Hz=${(1000 / fps).toFixed(0)}ms`).join(" · ")}
        </text>
        {configs.map((config, configIdx) => {
          const startX = groupCenter(configIdx) - ((barW + barGap) * fpsValues.length - barGap) / 2;
          return (
            <g key={config}>
              {fpsValues.map((fps, fpsIdx) => {
                const row = rows.find((item) => item.config === config && asNumber(item.target_fps) === fps);
                const p95 = asNumber(row?.p95_ms) ?? 0;
                const barH = plotH - (y(p95) - margin.top);
                const xPos = startX + fpsIdx * (barW + barGap);
                return (
                  <rect
                    key={fps}
                    x={xPos}
                    y={y(p95)}
                    width={barW}
                    height={barH}
                    rx="2"
                    fill={CHART_COLORS[fpsIdx % CHART_COLORS.length]}
                  >
                    <title>{`${config} @ ${fps} Hz: p95 ${p95.toFixed(4)} ms`}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}
        {fpsValues.map((fps, idx) => (
          <g key={fps} transform={`translate(${margin.left + idx * 110}, ${height - 6})`}>
            <rect x="0" y="-9" width="8" height="8" rx="2" fill={CHART_COLORS[idx % CHART_COLORS.length]} />
            <text x="12" y="0" className="fill-dam-muted text-[10px]">
              {fps} Hz
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function ExperimentCard({
  exp,
  params,
  result,
  running,
  progressText,
  onRun,
  onParamChange,
}: {
  exp: ExperimentDef;
  params: Record<string, unknown>;
  result: ExperimentResult | null;
  running: boolean;
  progressText?: string;
  onRun: () => void;
  onParamChange: (key: string, value: unknown) => void;
}) {
  const defaults = useMemo(
    () => Object.entries(exp.default_params ?? {}).filter(([key]) => key !== "outdir"),
    [exp.default_params],
  );
  const artifacts = visibleArtifacts(result);
  const previewArtifacts = primaryPreviewArtifacts(artifacts);

  return (
    <section className="panel p-4 space-y-4">
      <div className="flex flex-col lg:flex-row lg:items-start gap-4">
        <div className="flex-1 min-w-0 space-y-2">
          <div className="flex items-center gap-2">
            <FlaskConical size={15} className="text-dam-blue" />
            <h2 className="text-sm font-black text-dam-text uppercase tracking-wide">{exp.title}</h2>
            <span className="text-[10px] font-bold text-dam-blue bg-dam-blue/10 border border-dam-blue/30 rounded px-2 py-0.5">
              {exp.rq}
            </span>
          </div>
          <p className="text-xs text-dam-muted leading-relaxed max-w-3xl">{exp.description}</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 max-w-4xl">
            {defaults.map(([key, value]) => (
              <label key={key} className="space-y-1">
                <span className="text-[9px] font-bold uppercase tracking-wider text-dam-muted/70">
                  {key}
                </span>
                {typeof value === "boolean" ? (
                  <input
                    type="checkbox"
                    checked={Boolean(params[key])}
                    onChange={(e) => onParamChange(key, e.target.checked)}
                    className="block h-5 w-5 accent-dam-blue"
                  />
                ) : (
                  <input
                    value={String(params[key] ?? "")}
                    onChange={(e) => {
                      const text = e.target.value;
                      onParamChange(
                        key,
                        typeof value === "number" && text.trim() !== "" ? Number(text) : text,
                      );
                    }}
                    className="w-full rounded border border-dam-border bg-dam-surface-2 px-2 py-1 text-[11px] font-mono text-dam-text outline-none focus:border-dam-blue/50"
                  />
                )}
              </label>
            ))}
          </div>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-dam-blue/15 border border-dam-blue/40 text-dam-blue text-xs font-bold hover:bg-dam-blue/25 disabled:opacity-60 transition-colors"
        >
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {running ? "Running" : "Run"}
        </button>
      </div>
      {progressText && (
        <div className="rounded-lg border border-dam-blue/25 bg-dam-blue/10 px-3 py-2 text-xs font-mono text-dam-blue">
          {progressText}
        </div>
      )}

      {result && (
        <div className="space-y-3 pt-3 border-t border-dam-border/60">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2">
              <p className="section-label">Status</p>
              <p className="metric-value text-sm text-dam-text">{result.status}</p>
            </div>
            <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2">
              <p className="section-label">Elapsed</p>
              <p className="metric-value text-sm text-dam-text">{result.elapsed_sec.toFixed(2)} s</p>
            </div>
            <div className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2 min-w-0">
              <p className="section-label">Rows</p>
              <p className="metric-value text-sm text-dam-text">{result.rows.length}</p>
            </div>
          </div>
          <RowPreview result={result} />
          {exp.id === "latency-bench" && <LatencyGroupedBarChart rows={result.rows} />}
          {previewArtifacts.length > 0 && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              {previewArtifacts.map((path) => (
                <ArtifactPreview key={path} path={path} />
              ))}
            </div>
          )}
          {artifacts.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {artifacts.map((path) => (
                <span key={path} className="flex items-center gap-1.5 text-[10px] font-mono text-dam-muted bg-dam-surface-2 border border-dam-border rounded px-2 py-1">
                  <FileSpreadsheet size={10} />
                  {path}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ArtifactPreview({ path }: { path: string }) {
  const url = api.experimentArtifactUrl(path);
  if (isPreviewArtifact(path)) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="block bg-dam-surface-1 border border-dam-border rounded-lg overflow-hidden">
        <div className="px-2 py-1.5 border-b border-dam-border text-[10px] font-mono text-dam-muted truncate">{path}</div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={path} className="w-full max-h-80 object-contain bg-dam-surface-2" />
      </a>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-1.5 text-[10px] font-mono text-dam-muted bg-dam-surface-1 border border-dam-border rounded px-2 py-1 hover:text-dam-text"
    >
      <FileSpreadsheet size={10} />
      {path}
    </a>
  );
}

export default function ExperimentsPage() {
  const [experiments, setExperiments] = useState<ExperimentDef[]>([]);
  const [paramsById, setParamsById] = useState<Record<string, Record<string, unknown>>>({});
  const [results, setResults] = useState<Record<string, ExperimentResult>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [progressById, setProgressById] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"run" | "artifacts">("run");

  useEffect(() => {
    api
      .listExperiments()
      .then((data) => {
        setExperiments(data.experiments);
        setParamsById((prev) => ({
          ...Object.fromEntries(data.experiments.map((exp) => [exp.id, exp.default_params ?? {}])),
          ...prev,
        }));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run(exp: ExperimentDef) {
    setError(null);
    setRunningId(exp.id);
    setProgressById((prev) => ({ ...prev, [exp.id]: "Starting..." }));
    try {
      const params = paramsById[exp.id] ?? exp.default_params;
      if (exp.id === "latency-bench") {
        const fpsValues = parseFpsValues(params.fps_values);
        if (!fpsValues.length) {
          throw new Error("fps_values must include at least one frequency");
        }
        setResults((prev) => {
          const next = { ...prev };
          delete next[exp.id];
          return next;
        });
        const baseOutdir = String(params.outdir ?? exp.default_params?.outdir ?? "data/experiments/latency_bench");
        let combined: ExperimentResult | undefined;
        for (const [index, fps] of fpsValues.entries()) {
          setProgressById((prev) => ({
            ...prev,
            [exp.id]: `Running ${fps} Hz (${index + 1}/${fpsValues.length})`,
          }));
          const result = await api.runExperiment(exp.id, {
            ...params,
            fps_values: String(fps),
            outdir: `${baseOutdir}/${fps}hz`,
          });
          combined = mergeExperimentResult(combined, result);
          setResults((prev) => ({ ...prev, [exp.id]: combined as ExperimentResult }));
          setProgressById((prev) => ({
            ...prev,
            [exp.id]: `Finished ${fps} Hz (${index + 1}/${fpsValues.length})`,
          }));
        }
      } else {
        const result = await api.runExperiment(exp.id, params);
        setResults((prev) => ({ ...prev, [exp.id]: result }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunningId(null);
      setProgressById((prev) => {
        const next = { ...prev };
        delete next[exp.id];
        return next;
      });
    }
  }

  return (
    <PageShell title="Experiments" subtitle="Native evaluation runners and thesis metrics">
      <div className="space-y-4">
        <div className="flex gap-1 bg-dam-surface-2 border border-dam-border rounded-lg p-1 w-fit">
          {(["run", "artifacts"] as const).map((name) => (
            <button
              key={name}
              onClick={() => setTab(name)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold uppercase tracking-wider transition-colors ${
                tab === name
                  ? "bg-dam-blue/15 text-dam-blue border border-dam-blue/30"
                  : "text-dam-muted border border-transparent hover:text-dam-text"
              }`}
            >
              {name}
            </button>
          ))}
        </div>

        {error && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
            <AlertCircle size={13} />
            {error}
          </div>
        )}

        {tab === "run" ? (
          experiments.map((exp) => (
            <ExperimentCard
              key={exp.id}
              exp={exp}
              params={paramsById[exp.id] ?? exp.default_params}
              result={results[exp.id] ?? null}
              running={runningId === exp.id}
              progressText={progressById[exp.id]}
              onRun={() => run(exp)}
              onParamChange={(key, value) =>
                setParamsById((prev) => ({
                  ...prev,
                  [exp.id]: { ...(prev[exp.id] ?? exp.default_params), [key]: value },
                }))
              }
            />
          ))
        ) : (
          <section className="panel p-4 space-y-3">
            {Object.values(results).length === 0 ? (
              <p className="text-xs text-dam-muted">No artifacts yet.</p>
            ) : (
              Object.values(results).map((result) => {
                const artifacts = visibleArtifacts(result);
                return (
                <div key={result.id} className="bg-dam-surface-2 border border-dam-border rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-bold text-dam-text">{result.id}</p>
                    <span className="text-[10px] font-mono text-dam-muted">{result.outdir}</span>
                  </div>
                  {artifacts.length > 0 ? (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                      {artifacts.map((path) => (
                        <ArtifactPreview key={path} path={path} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-dam-muted">No displayed artifacts for this run.</p>
                  )}
                </div>
                );
              })
            )}
          </section>
        )}
      </div>
    </PageShell>
  );
}
