# PRD 20.10 — enterprise investigation workspace

Open **Governance and audit → Enterprise workspace** (`/governance/enterprise`), or the
**Collaboration** link from the working case. The offline room supports session-only comments,
mentions, assignments, evidence bookmarks, hypotheses, votes, review requests and conflict
resolution. Every demo is labeled; no communication is sent. A prepared briefing does not send
it to its recipient. The Operations and Release/residency tabs explain measured health and
release boundaries. Monitoring guidance is at `/governance/drift` and `docs/drift_monitoring.md`.

## Enable persistent case collaboration

1. Apply `supabase/migrations/20261018000002_enterprise_collaboration.sql` in a local/staging
   Supabase project after the existing migrations. This change does not apply hosted migrations.
2. Use `backend/.env.example` to configure `ENTERPRISE_ENABLED=true`,
   `CASE_AUTHORIZATION_ENABLED=true`, `DEPLOYMENT_REGION` and approved destinations. Restart
   the backend. Keep case authorization enabled even if the enterprise feature is later hidden.
3. An authenticated admin with an **aal2** Supabase token provisions investigators through
   `PUT /enterprise/users/{user_id}/attributes` with region, clearance and permitted purposes.
4. That admin configures each case through `PUT /enterprise/cases/{case_id}/policy` with region,
   classification, purpose and timezone-aware review date. The existing creator/assignee becomes
   the lead only after their attributes pass the policy. A case without a policy is inaccessible
   once case authorization is enabled; migrate/provision existing cases before rollout.
5. An authorized case lead/reviewer with aal2 adds members through
   `PUT /enterprise/cases/{case_id}/collaborators`. The new member's attributes must match.
6. In the frontend, expand **Connect an authorized case**, enter a provisioned user token and
   the backend case UUID, then load. The prototype sign-in and its role switch are independent
   of backend authorization. Tokens stay in page memory and clear when the page deactivates.

Worker shared keys do not confer a human case identity or case membership. Existing agent workers
need a delegated, authorized user identity for governed-case endpoints; broader service identity
and delegated access policies are not implemented. Model workers may still submit tenant-scoped
metrics/drift samples under the existing worker authentication contract.

## API contracts and workflow

| Endpoint | Behavior |
| --- | --- |
| `GET /enterprise/cases/{id}/room` | Case policy, current role, members, evidence and latest 100 immutable events. Reload explicitly to follow another investigator. |
| `POST /enterprise/cases/{id}/room/events` | Typed action, client UUID idempotency key and expected revision. Database transaction lock prevents concurrent writes at the same revision. HTTP 409 preserves the user's note for review/retry. |
| `POST /enterprise/cases/{id}/handoff` | Requires an already authorized `recipient_id`. Includes only supported factual claims from the latest narrative whose transaction references belong to this case's evidence. Hypotheses stay unverified pending questions. Nothing is sent. |
| `GET /enterprise/cases/{id}/evidence/v1?purpose=...` | Published `EvidenceBundleV1` OpenAPI schema: alerts, transactions, entities, case evidence, supported claims and dispositions with canonical IDs and a hash. Requires permitted purpose, lead/reviewer and aal2. |
| `POST /enterprise/metrics` | Admin/analyst/model-worker operation measurement with timezone-aware observed time and source reference. |
| `GET /enterprise/operations` | Latest 24 hours, at most 5,000 measured samples; explicit unknown/stale states, p95, target attainment and fallback. |
| `POST /enterprise/drift/check` | Bounded two-window drift and labeled-performance comparison; saves aggregate report, not feature values. |
| `GET /enterprise/drift/reports` | Latest 50 reports for this tenant. |
| `GET /enterprise/environment` | Declared storage locations, region and approved destinations. |
| `POST /enterprise/egress/check` | Denies unapproved destinations/regions and external transfer of real or restricted data. Preview only, sends no data. |
| `GET /enterprise/release` | Verifies a local manifest against current pinned files and configured trusted public key; missing/unsigned/unverified states remain explicit. |

Assignments/resolutions require a case lead/reviewer. Observers cannot write. Mentions and assignees
must pass the recipient's case policy. Bookmarks cannot cite another case's evidence; votes and
resolutions must reference matching hypotheses/conflicts in the same case. Control changes and
room actions create immutable audit entries. Retrying the same client key/payload returns the
original event; reusing a key with different content is a conflict. The client retains the key
across failed requests until the action is confirmed.

The existing `/cases`, `/cases/{id}`, `/agent-runs/{id}` and `/narratives/{id}` routes enforce
case authorization when enabled, including filtered case lists. Existing case reassignment
checks management authority and the recipient's case access. Both export routes require step-up;
the storage export also checks declared residency. Private Storage export reads require case
lead/reviewer membership and aal2; the earlier tenant-wide export policy is replaced. RLS independently enforces region, clearance,
purpose and membership on case material. Source/account/alert exploration APIs retain their
existing tenant-role scope; this is not a rewrite of institution-wide data classification.

Exports cap evidence and transactions at 500. Claims use at most 100 from the latest narrative;
decisions use the latest 100. Graph paths are explicitly unavailable in the v1 exchange schema.
No incumbent import adapters are shipped because their semantics have not been validated.

## Release signing, reset and smoke tests

From `backend/`, using the project's Python environment:

```sh
.venv/bin/python scripts/release.py smoke
.venv/bin/python scripts/release.py reset-demo
.venv/bin/python scripts/release.py create
.venv/bin/python scripts/release.py verify
```

`reset-demo` regenerates only synthetic frontend fixtures and checks pinned release inputs. It
never deletes/truncates tenant records. The in-app reset clears only the synthetic room session.
An unsigned manifest is labeled unsigned. To sign a demo release with a key outside the repository:

```sh
openssl genpkey -algorithm ED25519 -out /private/tmp/tracelayer-demo.private.pem
openssl pkey -in /private/tmp/tracelayer-demo.private.pem -pubout -out /private/tmp/tracelayer-demo.public.pem
.venv/bin/python scripts/release.py create --signing-key /private/tmp/tracelayer-demo.private.pem
.venv/bin/python scripts/release.py verify --public-key /private/tmp/tracelayer-demo.public.pem
```

Set `RELEASE_PUBLIC_KEY_PATH` to the separately trusted public key path for API verification.
Private keys are not created, stored or returned by the app. Do not treat a manifest's own key
as a trust anchor. The script pins prompt files, tool/evidence schemas, rules/features, model
artifacts, migration policies, dependency locks and declared dataset/runtime/policy manifests.
Missing artifacts/hosted versions remain unavailable; exact hosted-state replay is not claimed.
Added or changed pin inputs require a new manifest and review. Live signed configuration rollout
and key rotation remain deployment responsibilities.

## Verification and boundaries

Backend tests include positive/negative ABAC, room references, tenant scope, recipient filtering,
semantic verification failures, drift minimum samples, constant-reference shifts, delayed labels,
versioned export hash round trips, signature tampering and path traversal. Run `python -m pytest -q`.

The independent SQL denial/retry/Storage suite is `supabase/tests/enterprise_rls.sql`. Run it on a
disposable/local Supabase database after migrations; it creates fixture users/cases and rolls
back. For a small isolated PostgreSQL test without a server:

```sh
npm install --prefix /private/tmp/trojanhacks-sql-test @electric-sql/pglite
node backend/scripts/test-enterprise-db.mjs /private/tmp/trojanhacks-sql-test/node_modules/@electric-sql/pglite/dist/index.js
```

Run that last command from the repository root. It applies the real foundation (excluding unrelated
extensions) and enterprise migration to simplified case-schema fixtures, then runs the SQL suite.
It verifies PostgreSQL RLS and sensitive-export read policies on simplified tables, not hosted
Supabase deployment, actual Storage URL signing or multi-process load. Production Supabase migration/Storage verification is still required before rollout.

Operational telemetry records graph, policy retrieval, verifier and evidence-export request results;
HTTP-successful failed/flagged verification is recorded as a failure. Expected client denials and
validation errors are excluded from service SLO samples; timeout/rate-limit responses remain failures. Ingestion delay and scoring
latency need explicit worker measurements. Dependency health is inferred from samples, not active
probes. Residency is declared configuration, not a cloud-provider attestation. The enterprise export
path enforces approved locations, but independent agent/LLM processes need separate egress controls.
