import type { Tone } from "@/lib/types";

export const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-brand-tint text-brand-ink",
  warn: "bg-attention-tint text-attention-ink",
  info: "bg-select-tint text-select-ink",
  bad: "bg-danger-tint text-danger-ink",
  dark: "bg-ink text-white",
  blue: "bg-select text-white",
  mute: "bg-neutral-tint text-ink-2",
};
