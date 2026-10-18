const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Formats a dollar amount with cents, for example `$58,200.00`. */
export function formatMoney(value: number): string {
  return usd.format(value);
}

/** Formats a dollar amount without trailing `.00`, for graph labels. */
export function formatMoneyShort(value: number): string {
  return formatMoney(value).replace(/\.00$/, "");
}

/**
 * Deterministic, non-cryptographic hash used to *simulate* manifest checksums
 * in the prototype. Never present this as SHA-256 without the "(simulated)" label.
 */
export function simulatedHash(input: string, length = 64): string {
  let out = "";
  for (let round = 0; round < 8; round++) {
    let h = (0x811c9dc5 ^ Math.imul(round, 2654435761)) >>> 0;
    for (let i = 0; i < input.length; i++) {
      h ^= input.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    out += h.toString(16).padStart(8, "0");
  }
  return out.slice(0, length);
}

/** Demo clock: each audit sequence number advances the synthetic UTC clock by one minute. */
export function demoTimestamp(seq: number): string {
  const minutes = 14 * 60 + 2 + seq;
  const hh = String(Math.floor(minutes / 60) % 24).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  const ss = String((seq * 17) % 60).padStart(2, "0");
  return `2026-10-09 ${hh}:${mm}:${ss} UTC`;
}

export function percent(value: number, max = 1): string {
  const clamped = Math.max(0, Math.min(1, max === 0 ? 0 : value / max));
  return `${Math.round(clamped * 100)}%`;
}
