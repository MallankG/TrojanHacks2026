import assert from "node:assert/strict";
import { test } from "node:test";
import { describePii, findPii, GENERIC_ERROR, maskName, maskRecord, maskRef, maskRefsInText, redactError, redactText, safeText } from "./pii.ts";

test("maskRef keeps the prefix, last two characters and a distinguishing tag", () => {
  assert.equal(maskRef("ACC-7001"), "ACC-••01·ut");
  assert.equal(maskRef("ACC-8001"), "ACC-••01·oa");
  assert.notEqual(maskRef("ACC-7001"), maskRef("ACC-8001"));
  assert.equal(maskRef("CUST-011"), "CUST-••11·e1");
  assert.equal(maskRef("8f2a91c3"), "••••••c3·2a");
  assert.equal(maskRef("L-01"), "L-••·b9");
  assert.equal(maskRef(null), "Not recorded");
  assert.equal(maskRef("ACC-••01·ut"), "ACC-••01·ut");
});

test("redactText replaces each PII kind with a placeholder", () => {
  assert.equal(redactText("mail jane.doe+x@bank.co.uk now"), "mail [EMAIL] now");
  assert.equal(redactText("SSN 123-45-6789."), "SSN [SSN].");
  assert.equal(redactText("call (212) 555-0147 or 212-555-0147"), "call [PHONE] or [PHONE]");
  assert.equal(redactText("card 4111 1111 1111 1111"), "card [CARD]");
  assert.equal(redactText("IBAN GB82 WEST 1234 5698 7654 32"), "IBAN [IBAN]");
});

test("checksums prevent false positives", () => {
  assert.equal(redactText("ref 4111 1111 1111 1112"), "ref 4111 1111 1111 1112");
  assert.equal(redactText("GB00 WEST 1234 5698 7654 32"), "GB00 WEST 1234 5698 7654 32");
});

test("display mode does not read digit-only values as card or phone numbers", () => {
  assert.equal(redactText("4111111111111111", { display: true }), "4111111111111111");
  assert.equal(redactText("212 555 0147", { display: true }), "212 555 0147");
  assert.equal(redactText("card 4111111111111111", { display: true }), "card [CARD]");
  assert.equal(redactText("4111111111111111"), "[CARD]");
  assert.equal(safeText("1700000000000"), "1700000000000");
});

test("ordinary transaction data is left alone", () => {
  const text = "TX-90001 moved 48500.00 USD on 2026-10-01T09:05:00Z for CASE-0412 (AL-1027)";
  assert.equal(redactText(text), text);
  assert.deepEqual(findPii(text), []);
});

test("describePii lists what was found", () => {
  assert.equal(describePii(findPii("a@b.com and 212-555-0147 or 212-555-0148")), "1 email address and 2 phone numbers");
  assert.equal(describePii([]), "");
});

test("findPii counts each kind once per match", () => {
  assert.deepEqual(findPii("a@b.com, c@d.org and 123-45-6789"), [
    { kind: "email", count: 2 },
    { kind: "ssn", count: 1 },
  ]);
});

test("maskRefsInText masks account refs but not case, alert or transaction IDs", () => {
  assert.equal(
    maskRefsInText("TX-90001 moved funds from ACC-7001 to CUST-011 in CASE-0412"),
    "TX-90001 moved funds from ACC-••01·ut to CUST-••11·e1 in CASE-0412",
  );
  assert.equal(safeText("ACC-7001 emailed a@b.com"), "ACC-••01·ut emailed [EMAIL]");
  assert.equal(safeText("ACC-7001 paid ACC-8001", true, new Set(["ACC-7001"])), "ACC-7001 paid ACC-••01·oa");
  assert.equal(safeText("ACC-7001 emailed a@b.com", false), "ACC-7001 emailed [EMAIL]");
});

test("redactError hides database messages and long server faults, keeps actionable text", () => {
  assert.equal(redactError("x".repeat(200), 500), GENERIC_ERROR);
  assert.equal(redactError('invalid input syntax for type uuid: "ACC-7001"', 422), GENERIC_ERROR);
  assert.equal(redactError('duplicate key value violates unique constraint "x"', 409), GENERIC_ERROR);
  assert.equal(redactError("HS256 tokens require SUPABASE_JWT_SECRET", 401), "HS256 tokens require SUPABASE_JWT_SECRET");
  assert.equal(redactError("Graph engine unavailable", 503), "Graph engine unavailable");
  assert.equal(redactError("Account ACC-7001 not found", 404), "Account ACC-••01·ut not found");
  assert.equal(redactError("Could not create user a@b.com", 400), "Could not create user [EMAIL]");
});

test("maskRecord masks raw source rows by column meaning", () => {
  const row = {
    transaction_id: "DEMO-000001",
    amount: "115.04",
    origin_account: "CUST-011",
    origin_name: "Synthetic Customer 011",
    nameOrig: "C1231006815",
    description: "Refund to jane@x.com for ACC-7001",
    email: "jane@x.com",
    note: "call 212-555-0147",
  };
  assert.deepEqual(maskRecord(row), {
    transaction_id: "DEMO-000001",
    amount: "115.04",
    origin_account: "CUST-••11·e1",
    origin_name: "S•••••••• C••••••• 0••",
    nameOrig: "•••••••••15·y9",
    description: "Refund to [EMAIL] for ACC-••01·ut",
    email: "••••••••",
    note: "call [PHONE]",
  });
  assert.equal(maskRecord(row, false).origin_name, "Synthetic Customer 011");
  assert.equal(maskRecord(row, false).email, "[EMAIL]");
  assert.equal(maskName(""), "");
});
