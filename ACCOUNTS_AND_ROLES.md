# TraceLayer: Demo Accounts and Role Access Guide

This document contains pre-provisioned demo credentials for each operational role within TraceLayer (`demo-bank` tenant), backed by Supabase Auth and database-enforced Row-Level Security (RLS).

---

## 🔑 Pre-Provisioned Demo Credentials

| Role | Email | Password | Display Name | Primary Responsibilities |
|---|---|---|---|---|
| **Admin** | `admin@demo.test` | `TraceLayer2026!Demo` | C. Vance (Admin) | Platform administration, enterprise policies, model weights, user provisioning |
| **Lead Investigator** | `investigator@demo.test` | `TraceLayer2026!Demo` | D. Morgan (Lead Investigator) | End-to-end case investigations, money-flow graph exploration, evidence pinning, copilot Q&A |
| **Fraud / BSA Analyst** | `analyst@demo.test` | `TraceLayer2026!Demo` | M. Rivera (Fraud Analyst) | Ingestion review, alert triage, anomaly detection, initial case creation |
| **BSA Officer / Reviewer** | `reviewer@demo.test` | `TraceLayer2026!Demo` | J. Okafor (BSA Officer) | Mandatory four-eyes review, disposition sign-off, SAR filing approval, cryptographic audit chain |
| **Data Steward** | `steward@demo.test` | `TraceLayer2026!Demo` | P. Nair (Data Steward) | Ingestion governance, schema validation, data quality repairs, quarantined batch resolution |

> **Prototype / SSO Instant Access**: Click **"Continue with company SSO"** on the sign-in screen and enter prototype verification code: `123456`. You can also switch roles instantly using the top navigation bar.

---

## 🛡️ Role-Based Permission Matrix

TraceLayer enforces strict role boundaries in FastAPI (`backend/app/core/security.py`) and PostgreSQL RLS:

| Action / Capability | Admin | Lead Investigator | BSA Analyst | BSA Officer (Reviewer) | Data Steward |
|---|:---:|:---:|:---:|:---:|:---:|
| **Upload Transaction CSVs** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **Inspect / Fix Data Quality Rules** | ✅ | ❌ | ✅ | ❌ | ✅ |
| **Approve / Release Quarantined Data** | ✅ | ❌ | ❌ | ✅ | ✅ |
| **Graph Workbench & Path Tracing** | ✅ | ✅ | ✅ | ✅ (Read) | ❌ |
| **AI Copilot & Narrative Generation** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Initial Case Disposition** | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Four-Eyes Disposition Approval** | ✅ | ❌ | ❌ | ✅ | ❌ |
| **Policy & Detection Weight Tuning** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Append-Only Audit & Lineage Trace** | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 🚀 Recommended Hackathon Demo Flow by Role

1. **Sign in as Data Steward (`steward@demo.test`)**:
   - Navigate to `/data` (Data Governance & Ingestion).
   - Inspect batch `ach_2026_10_06_b` held in quarantine due to schema drift / format anomalies.
   - Run the data-quality rule repair to unlock downstream graph traversal.
2. **Sign in as Fraud Analyst (`analyst@demo.test`)**:
   - Navigate to `/alerts` and `/models`.
   - Inspect high-priority alert `AL-1027` on seed account `ACC-2208`.
   - Review the leak-free XGBoost score (`0.91`) and GATv2 GNN score (`0.94`).
   - Escalate to an official investigation case (`CASE-0412`).
3. **Sign in as Lead Investigator (`investigator@demo.test`)**:
   - Open `/graph` (Graph Investigation Workbench).
   - Trace the 5-hop layering chain highlighted in **vivid red lines** from `ACC-1104` through seed `ACC-2208` to cross-border exit `EXT-CB-77`.
   - Open the **Copilot** (`/copilot`) to generate the synthetic narrative using the local Ollama LLM + 9-rule deterministic verifier.
   - Submit recommended disposition: "File Suspicious Activity Report (SAR)".
4. **Sign in as BSA Officer (`reviewer@demo.test`)**:
   - Open `/cases` -> `CASE-0412`.
   - Perform mandatory supervisory four-eyes review.
   - Review and sign off the narrative.
   - Finalize decision and generate the Ed25519 hash-chained cryptographic audit receipt.
