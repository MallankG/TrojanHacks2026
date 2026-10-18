"use client";

import { useEffect, useMemo, useState } from "react";
import { modelForEdges } from "@/lib/domain/graph";
import { buildStoryboard } from "@/lib/domain/storyboard";
import { useDemoState, useEvidenceRegistry } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { CiteList } from "@/components/ui/cite";
import { Pill } from "@/components/ui/pill";
import { GraphCanvas } from "./graph-canvas";

const FRAME_MS = 2800;

/** Narrated, hop-by-hop storyboard of the verified path. Every sentence resolves to evidence. */
export function Storyboard() {
  const fixed = useDemoState().data.driftFixed;
  const registry = useEvidenceRegistry();
  const frames = useMemo(() => buildStoryboard(fixed, registry), [fixed, registry]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const current = Math.min(index, frames.length - 1);
  const frame = frames[current];
  const last = current >= frames.length - 1;
  const blocked = frames.filter((f) => f.issues.length > 0).length;

  // Playback stops by itself on the last frame.
  const isPlaying = playing && !last;

  useEffect(() => {
    if (!isPlaying) return;
    const timer = setTimeout(() => setIndex((i) => i + 1), FRAME_MS);
    return () => clearTimeout(timer);
  }, [isPlaying, current]);

  const model = useMemo(
    () => modelForEdges(frame.edges, { highlighted: frame.focus ? [frame.focus] : [], placeholder: frame.placeholder, tokenVisible: frame.edges.includes("e7") }),
    [frame],
  );

  const play = () => {
    if (last) {
      setIndex(0);
      setPlaying(true);
      return;
    }
    setPlaying(!isPlaying);
  };

  return (
    <div className="flex flex-wrap items-start gap-4">
      <Card className="flex min-w-0 flex-[3_1_600px] flex-col gap-3 p-3.5">
        <GraphCanvas model={model} fixed />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="dark" size="sm" onClick={play}>
            {isPlaying ? "Pause" : last ? "Replay" : "Play"}
          </Button>
          <Button variant="ghost" size="sm" disabled={current === 0} onClick={() => setIndex(current - 1)}>
            Previous
          </Button>
          <Button variant="ghost" size="sm" disabled={last} onClick={() => setIndex(current + 1)}>
            Next
          </Button>
          <span className="text-xs text-muted">
            Frame {current + 1} of {frames.length}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {frames.map((f, i) => (
            <Chip
              key={f.key}
              pressed={i === current}
              className="min-h-[30px] text-[11.5px]"
              onClick={() => {
                setPlaying(false);
                setIndex(i);
              }}
            >
              {i + 1}. {f.title}
            </Chip>
          ))}
        </div>
      </Card>
      <div className="flex flex-[1_1_340px] flex-col gap-4">
        <Panel aria-live="polite">
          <CardHeader eyebrow={`Frame ${current + 1} · ${frame.kind === "lead" ? "lead only" : frame.kind}`} title={frame.title} actions={<Pill tone={frame.issues.length ? "bad" : "ok"}>{frame.issues.length ? "Blocked" : "All claims resolve"}</Pill>} />
          {frame.issues.length > 0 ? (
            <Callout tone="bad" title="Narration blocked.">
              {frame.issues.join(" ")}
            </Callout>
          ) : (
            <ul className="flex flex-col gap-2">
              {frame.claims.map((c) => (
                <li key={c.text} className="text-[14px]">
                  {c.text}
                  <br />
                  <CiteList ids={c.cites} />
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-1.5">
            {frame.signals.map((s) => (
              <Pill key={s} tone="info">
                {s}
              </Pill>
            ))}
          </div>
          <Callout tone="mute" title="Caveat.">
            {frame.caveat.text} <CiteList ids={frame.caveat.cites} />
          </Callout>
        </Panel>
        <Callout tone={blocked ? "bad" : "info"}>
          {blocked
            ? `${blocked} frame(s) blocked by the verifier.`
            : `Verifier: every claim in all ${frames.length} frames cites evidence in the registry, and every amount matches a cited transaction or a declared derivation.`}
          {fixed ? "" : " The story stops at the data hold until it is resolved."}
        </Callout>
      </div>
    </div>
  );
}
