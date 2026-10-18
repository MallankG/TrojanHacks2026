# Privacy and PII handling

TraceLayer runs on synthetic data, but it is built to handle real customer data safely.
This page lists which data is personal, where it lives, and what protects it at each layer.
The live state of each browser control is shown under **Governance → Settings → Privacy and PII controls**.

## What counts as personal data

| Data | Where it is stored | Why we keep it |
|---|---|---|
| `origin_account`, `destination_account` (account references) | `accounts.account_ref`, `transactions` | Needed to build the money-flow graph |
| `origin_name`, `destination_name` (party names) | `counterparties.raw_name`, `normalized_name` | Entity resolution only |
| `description` (free-text memo) | `transactions.description`, capped at 1000 characters | Context for investigators; treated as untrusted input |
| `customer_ref`, `risk_context` (KYC hooks) | `accounts` | Context for the account profile |
| Raw CSV rows | `source_rows.raw_data`, plus the original file in the private `source-files` bucket | Lineage: every alert can be traced back to its source row |
| Narratives, collaborator notes, decision notes | `case_narratives`, `case_room_events`, `case_decisions` | The case record |
| User email | `auth.users` and `profiles` | Sign-in |

## Browser controls (`frontend/src/lib/privacy/`)

All rules live in one module, `pii.ts`. It has no dependencies and is unit-tested with `npm test`.

1. **Account identifiers are masked by default.** Every account reference in the app is shown with only its prefix and last two characters, for example `ACC-7001` → `ACC-••01`. This covers live and demo views, graph labels, tables, search results and aria-labels.
   - Users with case access (not auditors or data stewards) can reveal one value. The value is masked again after 30 seconds.
   - The top-bar toggle unmasks everything for the current page session only.
   - Masking state is held in memory, so reloading, signing in or switching identity always starts masked again.
2. **Free text is filtered for PII.** Shared display components (`Callout`, `KeyValueList`, `CardHeader`, `PageHeader`) and every narrative, note, memo and alert explanation pass through `SensitiveText`.
   - It replaces emails, US SSNs, phone numbers, card numbers and IBANs with placeholders such as `[EMAIL]`.
   - Card numbers must pass a Luhn check and IBANs a mod-97 check, so amounts, dates and transaction IDs are left alone.
3. **Raw source rows are masked by column meaning.** Party names become initials (`Synthetic Customer 011` → `S•••••••• C••••••• 0••`), account columns are masked, contact columns (email, phone, address, SSN, date of birth) are hidden, and memo columns are filtered. The upload preview masks values the same way and labels sensitive canonical fields.
4. **Personal data is held back before it leaves the browser.** Copilot questions go to a language model, and collaborator notes are append-only, so both are checked before sending. If PII is found, nothing is sent until the user edits the text or chooses **Send redacted**.
5. **Sessions are kept out of persistent storage.** Supabase tokens are kept in `sessionStorage` (one tab, cleared when the tab closes) instead of `localStorage`. The stored copy never includes the user's email. Sessions saved in `localStorage` by earlier builds are removed on load.
6. **Error messages are sanitized.** Server faults (5xx) and raw database messages, which can echo submitted values, are replaced with a generic message. The request ID is kept so an error can still be matched to the audit trail. The error page logs only the error type and digest.
7. **Security headers are set** (`next.config.ts`):
   - `Referrer-Policy: no-referrer`, so case and account URLs are never sent to other sites
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - a restrictive `Permissions-Policy`

## Backend controls already in place

- **Tenant isolation:** row-level security on every table. Reads are limited to the caller's tenant, and browsers cannot write directly; all writes go through the backend (`supabase/migrations/20261017000007_rls_policies.sql`).
- **Private storage:** the `source-files` and `case-exports` buckets are private and readable per tenant prefix. Downloads use short-lived signed URLs.
- **Role checks on the server:** every role check is enforced again on the backend. With enterprise mode on, sensitive evidence exports require a case lead or reviewer with step-up (aal2) authentication.
- **Language-model prompts:** narrative prompts are built from schemas that reject unknown fields (`extra="forbid"`), so party names and memos never reach the model. Prompts are logged only as a version and an input hash, never in full.
- **Logs:** request metrics never store URLs, query strings, tokens or bodies (`backend/app/services/telemetry.py`). Governance receipts exclude raw values.

## Known gaps and follow-ups

The current scope is frontend-only, with no database schema changes. These items remain on the backend:

- **Retention:** raw rows (`source_rows.raw_data`) and uploaded files have no retention window or purge job yet. Append-only tables (audit, collaborator notes) cannot be erased.
- **Server-side error sanitization:** `backend/app/core/errors.py` returns PostgREST messages, and a few routes return `str(exc)`. The browser hides these, but other API clients would still see them.
- **Export masking:** evidence-package CSVs include account references and memos in full. Exports are signed, hashed and limited by role, so masking would need a versioned export format.
- **Copilot input at the agent layer:** the browser redacts questions, but the agent service should redact again before calling the model. `OLLAMA_HOST` should be limited to localhost or an HTTPS allowlist.
- **Pattern-based detection:** PII detection uses patterns and checksums. It will not catch personal data written in prose, such as a name in a memo.
