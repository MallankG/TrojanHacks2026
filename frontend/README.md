# TraceLayer frontend

Next.js 16 (App Router, Cache Components) + React 19 + TypeScript + Tailwind CSS v4.
The UI runs entirely on seeded **synthetic** data so the full investigation flow can be demoed without the backend.

```bash
npm install
npm run dev        # http://localhost:3000
npm run build && npm start
npm run lint
```

Optional: set `NEXT_PUBLIC_API_BASE_URL` (for example `http://127.0.0.1:8000`) to show live backend health in the sidebar. All screens keep working on demo data when the API is offline.

## Demo path

Sign in with any work email, a password of 8+ characters and code `123456`.

1. **Data operations** – approve mapping fix v4.1 for the quarantined batch, inspect bad rows, onboard a CSV.
2. **Risk intelligence** – AL-1027 re-scores to *Review first*; signals are shown separately.
3. **Graph workbench** – play the timeline, expand ACC-2208, save the evidence subgraph.
4. **AI copilot** – use the prompt chips; the injected false amount is blocked by the verifier.
5. **Cases** – use the verified value, record a disposition, switch the Role menu to *BSA officer* to approve, generate the bundle and verify its hash.
6. **Governance** – lineage, model registry, drift, agents, audit trail, exports and policy settings.

The sidebar's prototype controls simulate LLM and GraphBase outages, a session timeout and a full reset.

## Live mode (real backend)

There is one UI. When the backend is configured, signing in with **email and password** uses Supabase Auth and the
screens that have a backend show live data next to the seeded samples (tagged **Live** / **Sample**). "Continue with
company SSO" still opens the seeded demo with code `123456`.

| Screen | Live from the backend | Still seeded (no backend yet) |
|---|---|---|
| Sign-in, role, tenant | Supabase login; role and tenant from the profile (role menu locked) | SSO + code path |
| Data operations | Batches = ingestion runs; contract checks from data-quality rules; **Upload CSV batch** uploads for real | Source feeds, onboarding wizard, mapping fix |
| Quality exceptions | Row-level findings per run, source row + downstream impact, steward acknowledge / waive | Scripted repair flows |
| Risk intelligence | Alerts from the latest ranker run: signals, graph, timeline, lineage, open a case, dismiss | Seeded queue, scoring weights, detector comparison |
| Cases | Live cases: create, notes, send for review, reviewer decision + disposition, evidence export, case audit | Seeded CASE-0412 workflow (narrative, checklist, report) |
| Governance → audit | The database's append-only audit log | Other governance tabs |
| Command center, graph workbench, entity profile, copilot | | Seeded (agents and graph service not wired yet) |

1. `cp .env.example .env.local` (all values are public; the service-role key never goes here).
2. Start the backend (`backend/README.md`) and create test users with `python -m scripts.create_user`.
3. `npm run dev`, open http://localhost:3000 and sign in with a test user's email and password.

Roles come from the database: `analyst`/`investigator` open cases and add notes, `reviewer` records decisions
(four-eyes), `data_steward` triages data-quality issues, `admin` can do everything. Alerts appear once the model
worker publishes a completed ranker run; copilot and narrative drafting arrive with the agent service.

## Structure

```
src/
  app/                    routes (one folder per screen; tabs are nested routes)
    sign-in/              sign-in with code step
    (workspace)/          authenticated shell: command, data, risk, graph,
                          entities, copilot, cases, governance
  components/
    ui/                   design-system primitives (Button, Pill, Card, Dialog, Field, Table…)
    shell/                sidebar, top bar, banners, case context bar, toaster
    modals/               dialogs rendered by the shell's ModalHost
  features/<screen>/      screen-level components
  lib/
    store/                client store (useSyncExternalStore + localStorage), actions with role guards
    domain/               pure business logic (priority, graph, evidence, copilot, case, governance)
    seed/                 synthetic seed data
    api/                  typed backend client and health check
```

Design tokens live in `src/app/globals.css` (`@theme`). Status is always written in words; color only supports it.

TraceLayer is decision support for human review. It never files SARs, freezes accounts or decides guilt.
