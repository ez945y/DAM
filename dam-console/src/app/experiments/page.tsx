"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, FileSpreadsheet, FlaskConical, Loader2, Play } from "lucide-react";
import { PageShell } from "@/components/PageShell";
import { api } from "@/lib/api";
import type { ExperimentDef, ExperimentResult } from "@/lib/types";

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function RowPreview({ result }: { result: ExperimentResult }) {
  const rows = result.rows.slice(0, 8);
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

function ExperimentCard({
  exp,
  result,
  running,
  onRun,
}: {
  exp: ExperimentDef;
  result: ExperimentResult | null;
  running: boolean;
  onRun: () => void;
}) {
  const defaults = useMemo(
    () => Object.entries(exp.default_params ?? {}).filter(([key]) => key !== "outdir"),
    [exp.default_params],
  );

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
          <div className="flex flex-wrap gap-1.5">
            {defaults.map(([key, value]) => (
              <span key={key} className="text-[10px] font-mono text-dam-muted bg-dam-surface-2 border border-dam-border rounded px-2 py-1">
                {key}={String(value)}
              </span>
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
          <div className="flex flex-wrap gap-2">
            {result.artifacts.map((path) => (
              <span key={path} className="flex items-center gap-1.5 text-[10px] font-mono text-dam-muted bg-dam-surface-2 border border-dam-border rounded px-2 py-1">
                <FileSpreadsheet size={10} />
                {path}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function ArtifactPreview({ path }: { path: string }) {
  const url = api.experimentArtifactUrl(path);
  const lower = path.toLowerCase();
  if (lower.endsWith(".svg") || lower.endsWith(".png")) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="block bg-dam-surface-1 border border-dam-border rounded-lg overflow-hidden">
        <div className="px-2 py-1.5 border-b border-dam-border text-[10px] font-mono text-dam-muted truncate">{path}</div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={path} className="w-full max-h-80 object-contain bg-black" />
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
  const [results, setResults] = useState<Record<string, ExperimentResult>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"run" | "artifacts">("run");

  useEffect(() => {
    api
      .listExperiments()
      .then((data) => setExperiments(data.experiments))
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run(exp: ExperimentDef) {
    setError(null);
    setRunningId(exp.id);
    try {
      const result = await api.runExperiment(exp.id, exp.default_params);
      setResults((prev) => ({ ...prev, [exp.id]: result }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunningId(null);
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
              result={results[exp.id] ?? null}
              running={runningId === exp.id}
              onRun={() => run(exp)}
            />
          ))
        ) : (
          <section className="panel p-4 space-y-3">
            {Object.values(results).length === 0 ? (
              <p className="text-xs text-dam-muted">No artifacts yet.</p>
            ) : (
              Object.values(results).map((result) => (
                <div key={result.id} className="bg-dam-surface-2 border border-dam-border rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-bold text-dam-text">{result.id}</p>
                    <span className="text-[10px] font-mono text-dam-muted">{result.outdir}</span>
                  </div>
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                    {result.artifacts.map((path) => (
                      <ArtifactPreview key={path} path={path} />
                    ))}
                  </div>
                </div>
              ))
            )}
          </section>
        )}
      </div>
    </PageShell>
  );
}
