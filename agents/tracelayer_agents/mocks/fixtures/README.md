# Mock fixtures

All data here is **synthetic** (accounts `ACC-xxxx`, transactions `TX-xxxxx`). No real people,
accounts or institutions. Regenerate the CSVs with:

```bash
python tracelayer_agents/mocks/fixtures/_generate.py   # deterministic; re-running gives identical files
```

## Transaction CSVs

Columns are the PRD §13 `transactions` table: `transaction_id, source_row_ref, timestamp, amount,
currency, channel, direction, origin_account_id, destination_account_id, transaction_status`.
Timestamps are ISO 8601 UTC (`Z`), amounts are decimal strings, and `source_row_ref` is `row_NNNN`
(the 1-based data row).

Each file has about 50 benign background transactions over 2026-09-01 to 2026-09-07: 14 retail
customers (`ACC-1001`–`ACC-1014`) paying 6 merchants (`ACC-1015`–`ACC-1020`) by card or ACH, plus
a few small customer-to-customer transfers. Background IDs are `TX-11xxx` (fan_out), `TX-12xxx`
(pass_through), `TX-13xxx` (cycle) and `TX-14xxx` (bad_batch), so batches never share an ID.

`accounts.csv` is the account master used for referential-integrity checks. `ACC-9999` is
deliberately missing from it.

## Planted transactions

Tests read this table, so keep it in sync with `_generate.py`. The first column is the fixture
file name without `.csv`.

| scenario | pattern | planted tx IDs |
|---|---|---|
| fan_out | ACC-3001 sends 4,775–4,990 USD to 9 different accounts (ACC-3101–ACC-3109) over 2h16m on 2026-09-04 | TX-51001, TX-51002, TX-51003, TX-51004, TX-51005, TX-51006, TX-51007, TX-51008, TX-51009 |
| pass_through | ACC-2001 → ACC-2002 (12,500.00) then ACC-2002 → ACC-2003 (12,350.00) 7 min later; ACC-2004 → ACC-2005 (8,000.00) then ACC-2005 → ACC-2006 (7,920.00) 11 min later | TX-52001, TX-52002, TX-52003, TX-52004 |
| cycle | ACC-4001 → ACC-4002 → ACC-4003 → ACC-4004 → ACC-4001, 25,000.00 decaying to 23,800.00 over 2026-09-02 to 2026-09-03, in time order | TX-53001, TX-53002, TX-53003, TX-53004 |
| bad_batch | rows that break the data contract (see below) | TX-54001, TX-54002, TX-54003, TX-54004, TX-54005, TX-54006 |

### bad_batch.csv defects

| tx ID | source row | defect | quality rule | severity | ingestion |
|---|---|---|---|---|---|
| TX-54001 | row_0021, row_0022 | duplicate `transaction_id` (amounts 1,200.00 and 1,250.00) | `transaction_id_unique` | critical | first row accepted, second rejected |
| TX-54002 | row_0024 | empty `destination_account_id` | `required_field_present` | critical | rejected |
| TX-54003 | row_0025 | destination `ACC-9999` is not in `accounts.csv` | `account_exists` | critical | accepted (flagged by quality) |
| TX-54004 | row_0028 | timestamp `2026-09-03T17:15:00+05:00` is not UTC | `timestamp_utc` | warning | accepted, normalized to 12:15Z |
| TX-54005 | row_0001 | timestamp `2026-02-30T10:00:00Z` is not a real date | `timestamp_iso8601_tz` | critical | rejected |
| TX-54006 | row_0026 | currency `US$` is not ISO 4217 | `currency_iso4217` | critical | rejected |

So `bad_batch.csv` has 57 rows: 53 accepted, 4 rejected, and the quality report is `fail`.

## policy_chunks.json

Eight short chunks for `MockPolicyRetriever`. Six summarize public U.S. sources (FFIEC BSA/AML
Examination Manual, 31 CFR 1020.320 and FinCEN's SAR narrative guidance); each `source_uri` points
at the public source. Their excerpts are **paraphrases written for this mock, not source text**,
and each says so. The real RAG corpus must store verbatim passages (see `PolicyRetriever` in
`interfaces/services.py`). The FFIEC chunks cite the manual's root URL because the section pages
can't be fetched automatically to confirm their paths.

The last two chunks (`mock_fictional_*`) are a **fictional** bank procedure for a made-up
"Example Community Bank". They use `urn:tracelayer:fictional:` URIs and say FICTIONAL in the title
and text.

Extra fields (`source_type`, `keywords`) are ignored by `RetrievedChunk`. `source_type` is one of
`regulatory_guidance`, `regulation` or `fictional_internal_procedure` and can be used as a filter.

## case_library.json

Ten **synthetic** closed cases (`SYN-CASE-001`–`SYN-CASE-010`) for the chat's `similar_cases` tool:
- Each has a typology, rail, `closed_at`, flow motifs, flow shape, graph features, a short
  descriptive summary and a prior disposition.
- No customer, account or transaction ids appear in them. They are reference profiles, not other
  cases' data.

## shared_tokens.json

Synthetic device fingerprints, login IPs (masked) and payment tokens for `shared_token_search`.
- Three link accounts of the fan_out case (`ACC-3001`/`ACC-3104`/`ACC-3107`,
  `ACC-3102`/`ACC-3105`, `ACC-3103`/`ACC-3108`).
- `TKN-IP-0B24` also includes an account outside every case (`ACC-9001`), so tests can check that
  outside accounts are only counted, never named.
- The rest link accounts of other scenarios.
