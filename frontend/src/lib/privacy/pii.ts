/**
 * PII masking, redaction and detection for everything the UI displays or sends.
 * Pure functions with no imports, so they run unchanged under `node --test`.
 *
 * Email, SSN and phone patterns match the backend's DOCUMENT_PII
 * (backend/app/services/governance_superpowers.py) so both sides flag the same values.
 */

export const MASK = "•";

export type PiiKind = "email" | "ssn" | "card" | "iban" | "phone";

export const PII_LABEL: Record<PiiKind, string> = {
  email: "email address",
  ssn: "Social Security number",
  card: "card number",
  iban: "IBAN",
  phone: "phone number",
};

interface Detector {
  kind: PiiKind;
  pattern: RegExp;
  /** Extra check to cut false positives (checksums). */
  valid?: (match: string) => boolean;
}

function luhn(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 13 || digits.length > 19) return false;
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return sum % 10 === 0;
}

function ibanChecksum(value: string): boolean {
  const compact = value.replace(/\s/g, "").toUpperCase();
  if (compact.length < 15 || compact.length > 34) return false;
  const rearranged = compact.slice(4) + compact.slice(0, 4);
  let remainder = 0;
  for (const ch of rearranged) {
    const code = ch >= "A" && ch <= "Z" ? String(ch.charCodeAt(0) - 55) : ch;
    for (const digit of code) remainder = (remainder * 10 + Number(digit)) % 97;
  }
  return remainder === 1;
}

// Order matters: SSN and card numbers are removed before the looser phone pattern runs.
const DETECTORS: Detector[] = [
  { kind: "email", pattern: /[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g },
  { kind: "ssn", pattern: /\b\d{3}-\d{2}-\d{4}\b/g },
  { kind: "card", pattern: /\b\d(?:[ -]?\d){12,18}\b/g, valid: luhn },
  { kind: "iban", pattern: /\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b/g, valid: ibanChecksum },
  { kind: "phone", pattern: /(?:\+1[ .-]?)?(?:\(\d{3}\)\s?|\b\d{3}[- .])\d{3}[- .]\d{4}\b/g },
];

const PLACEHOLDER: Record<PiiKind, string> = { email: "[EMAIL]", ssn: "[SSN]", card: "[CARD]", iban: "[IBAN]", phone: "[PHONE]" };

/** Replace emails, SSNs, card numbers, IBANs and phone numbers with typed placeholders. */
export function redactText(text: string): string {
  let out = text;
  for (const { kind, pattern, valid } of DETECTORS) {
    out = out.replace(pattern, (match) => (!valid || valid(match) ? PLACEHOLDER[kind] : match));
  }
  return out;
}

/** Which kinds of PII appear in `text`, and how often. Empty when none. */
export function findPii(text: string): Array<{ kind: PiiKind; count: number }> {
  const found: Array<{ kind: PiiKind; count: number }> = [];
  let rest = text;
  for (const { kind, pattern, valid } of DETECTORS) {
    let count = 0;
    rest = rest.replace(pattern, (match) => {
      if (valid && !valid(match)) return match;
      count++;
      return " ";
    });
    if (count) found.push({ kind, count });
  }
  return found;
}

/** "1 email address and 2 phone numbers" — for warnings shown before text leaves the browser. */
export function describePii(found: Array<{ kind: PiiKind; count: number }>): string {
  const parts = found.map(({ kind, count }) => `${count} ${PII_LABEL[kind]}${count === 1 ? "" : "s"}`);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : (parts[0] ?? "");
}

/**
 * Mask an account-style reference, keeping a short alphabetic prefix and the last two characters:
 * `ACC-7001` → `ACC-••01`, `CUST-011` → `CUST-•11`, `8f2a91c3` → `••••••c3`.
 */
export function maskRef(ref: string | null | undefined): string {
  if (!ref) return "Not recorded";
  if (ref.includes(MASK)) return ref;
  const prefix = /^[A-Za-z]{1,8}-/.exec(ref)?.[0] ?? "";
  const body = ref.slice(prefix.length);
  if (!body) return MASK.repeat(ref.length);
  const keep = body.length > 2 ? 2 : 0;
  return prefix + MASK.repeat(body.length - keep) + body.slice(body.length - keep);
}

// Account-style tokens embedded in sentences. Case, alert, transaction and batch IDs are not personal data.
const REF_IN_TEXT = /\b(?:ACC|CUST|MER|CASHDESK|L|R|ST)-[A-Z0-9][A-Z0-9-]*\b/g;

/** Mask account references embedded in free text such as narratives and search descriptions. */
export function maskRefsInText(text: string): string {
  return text.replace(REF_IN_TEXT, (match) => maskRef(match));
}

/** Everything the UI shows from free text: PII redacted and account references masked. */
export function safeText(text: string | null | undefined, masked = true): string {
  if (!text) return "";
  const redacted = redactText(text);
  return masked ? maskRefsInText(redacted) : redacted;
}

/** Keep each word's first character: `Synthetic Customer 011` → `S•••••••• C••••••• 0••`. */
export function maskName(name: string | null | undefined): string {
  if (!name) return "";
  return name.replace(/\S+/g, (word) => word[0] + MASK.repeat(word.length - 1));
}

// Column names in raw source rows, checked in this order (IBM's nameOrig/nameDest are account IDs).
const CONTACT_KEY = /e-?mail|phone|mobile|address|ssn|social|tax.?id|passport|birth|dob/i;
const ACCOUNT_KEY = /account|acct|iban|customer|sender|receiver|beneficiary|nameorig|namedest|^(from|to)$/i;
const NAME_KEY = /name/i;
const FREE_TEXT_KEY = /desc|memo|narrative|remittance|note|comment|reference/i;

/**
 * Mask a raw source row for display. PII patterns are always redacted; with `masked`, contact
 * fields are hidden, account references masked and party names reduced to initials.
 */
export function maskRecord(record: Record<string, unknown> | null | undefined, masked = true): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(record ?? {})) {
    const value = raw === null || raw === undefined ? "" : String(raw);
    if (!value || !masked) out[key] = redactText(value);
    else if (CONTACT_KEY.test(key)) out[key] = MASK.repeat(8);
    else if (ACCOUNT_KEY.test(key)) out[key] = maskRef(value);
    else if (NAME_KEY.test(key)) out[key] = maskName(value);
    else if (FREE_TEXT_KEY.test(key)) out[key] = safeText(value);
    else out[key] = redactText(value);
  }
  return out;
}

/** Canonical transaction fields that carry personal data (canonical-txn-v1), with why they are sensitive. */
export const SENSITIVE_FIELDS: Record<string, string> = {
  origin_account: "Account identifier",
  destination_account: "Account identifier",
  origin_name: "Party name",
  destination_name: "Party name",
  description: "Free text, may contain PII",
};

const DATABASE_MESSAGE = /invalid input syntax|violates|duplicate key|null value in column|relation "|column "|syntax error|permission denied|PGRST|JWT|stack|Traceback/i;

export const GENERIC_ERROR = "The server could not complete this request.";

/**
 * Error text that is safe to show: server faults and raw database messages (which can echo the
 * submitted values) become a generic message; anything else has PII and account references removed.
 */
export function redactError(message: string, status: number): string {
  if (status >= 500 || DATABASE_MESSAGE.test(message)) return GENERIC_ERROR;
  return safeText(message).slice(0, 300);
}
