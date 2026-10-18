"use client";

import { useState } from "react";
import { call, errorMessage, invalidateLive } from "@/lib/live/api";
import { words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { toast } from "@/lib/store/actions";
import { Button } from "@/components/ui/button";

interface DetectionSummary {
  ranker_run_id: string;
  transactions_considered: number;
  rule_signals: number;
  anomaly_signals: number;
  supervised: { signals: number; skipped_reason: string | null };
  alerts: number;
  alerts_by_typology: Record<string, number>;
}

/** Runs the backend detection pipeline (typology rules + anomaly baseline + XGBoost when available). */
export function RunDetectionButton() {
  const profile = useLiveProfile();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  // Same roles the backend allows (admin, analyst); scoring writes are not a reviewer action.
  if (!profile || !(profile.role === "admin" || profile.role === "analyst")) return null;

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      const s = await call<DetectionSummary>("/scoring/run", { method: "POST", body: {}, timeoutMs: 180_000 });
      const typologies = Object.entries(s.alerts_by_typology).map(([t, n]) => `${words(t)} ${n}`).join(", ");
      const text = `${s.alerts} alert(s) from ${s.transactions_considered.toLocaleString("en-US")} transactions${typologies ? `: ${typologies}` : ""}.`;
      setResult(text);
      toast(`Detection finished. ${text}`);
      for (const key of ["alerts", "alert:", "dashboard", "registry", "pipelines", "detectors"]) invalidateLive(key);
    } catch (problem) {
      setResult(`Detection failed: ${errorMessage(problem)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={run} disabled={busy} title={LIVE_ROLES[profile.role].label}>
        {busy ? "Running detection" : "Run detection"}
      </Button>
      {result ? <span className="max-w-[420px] text-right text-xs text-muted">{result}</span> : null}
    </div>
  );
}
