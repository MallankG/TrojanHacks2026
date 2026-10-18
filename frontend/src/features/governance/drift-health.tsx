"use client";

import { featureDrift, SCORE_HISTOGRAM } from "@/lib/domain/governance";
import { useDemoState } from "@/lib/store/hooks";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Legend, Meter, Swatch } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";

const PSI_SCALE_MAX = 0.4;
const THRESHOLD_BIN = 6;
const PSI_COLORS = { Stable: "var(--color-brand)", Watch: "var(--color-attention-stroke)", Drift: "var(--color-danger)" } as const;

export function DriftHealth() {
  const fixed = useDemoState().data.driftFixed;
  const drift = featureDrift(fixed);
  const peak = Math.max(...SCORE_HISTOGRAM);

  return (
    <>
      <Callout tone="mute">Synthetic monitoring sample. Thresholds: 0.10 watch, 0.25 drift (population stability index).</Callout>
      <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
        <Panel>
          <CardHeader title="Feature drift (PSI)" description="Compared with the training window. Missing-rate drift follows the schema change." />
          <ul className="flex flex-col gap-2.5">
            {drift.map((row) => (
              <li key={row.feature} className="grid grid-cols-[minmax(150px,1fr)_1.2fr_44px_64px] items-center gap-2 text-[12.5px]">
                <span className="break-all font-mono">{row.feature}</span>
                <Meter value={row.psi / PSI_SCALE_MAX} color={PSI_COLORS[row.status]} label={`${row.feature} PSI ${row.psi.toFixed(2)}`} />
                <b>{row.psi.toFixed(2)}</b>
                <Pill tone={row.tone}>{row.status}</Pill>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel>
          <CardHeader title="XGBoost score distribution" description="Last 7 days. Dark bars are at or above the 0.62 threshold." />
          <figure className="flex flex-col gap-1.5">
            <div className="flex h-[140px] items-end gap-1.5 border-b border-[#d5dce6]" role="img" aria-label={`Score histogram, ten bins from 0.0 to 1.0. Counts: ${SCORE_HISTOGRAM.join(", ")}.`}>
              {SCORE_HISTOGRAM.map((count, i) => (
                <div key={i} className="flex-1 rounded-t-[3px]" style={{ height: `${Math.round((count / peak) * 120)}px`, background: i >= THRESHOLD_BIN ? "var(--color-ink)" : "#9fb0c6" }} />
              ))}
            </div>
            <div className="flex gap-1.5" aria-hidden="true">
              {SCORE_HISTOGRAM.map((_, i) => (
                <span key={i} className="flex-1 text-center text-xs text-muted">
                  {(i / 10).toFixed(1)}
                </span>
              ))}
            </div>
          </figure>
          <Legend
            items={[
              { label: "Below threshold", swatch: <Swatch color="#9fb0c6" /> },
              { label: "At or above 0.62", swatch: <Swatch color="var(--color-ink)" /> },
            ]}
          />
        </Panel>
      </div>
    </>
  );
}
