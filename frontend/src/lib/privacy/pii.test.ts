import assert from "node:assert/strict";
import { test } from "node:test";
import { describePii, findPii, GENERIC_ERROR, maskName, maskRecord, maskRef, maskRefsInText, redactError, redactText, safeText } from "./pii.ts";

test("maskRef keeps the prefix and last two characters", () => {
  assert.equal(maskRef("ACC-7001"), "ACC-••01");
  assert.equal(maskRef("CUST-011"), "CUST-•11");
  assert.equal(maskRef("8f2a91c3"), "••••••c3");
  assert.equal(maskRef("L-01"), "L-••");
  assert.equal(maskRef("ACC-"), "••••");
  assert.equal(maskRef(null), "Not recorded");
  assert.equal(maskRef("ACC-••01"), "ACC-••01");
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
    "TX-90001 moved funds from ACC-••01 to CUST-•11 in CASE-0412",
  );
  assert.equal(safeText("ACC-7001 emailed a@b.com"), "ACC-••01 emailed [EMAIL]");
  assert.equal(safeText("ACC-7001 emailed a@b.com", false), "ACC-7001 emailed [EMAIL]");
});

test("redactError hides server faults and database messages", () => {
  assert.equal(redactError("anything", 500), GENERIC_ERROR);
  assert.equal(redactError('invalid input syntax for type uuid: "ACC-7001"', 422), GENERIC_ERROR);
  assert.equal(redactError('duplicate key value violates unique constraint "x"', 409), GENERIC_ERROR);
  assert.equal(redactError("Account ACC-7001 not found", 404), "Account ACC-••01 not found");
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
    origin_account: "CUST-•11",
    origin_name: "S•••••••• C••••••• 0••",
    nameOrig: "•••••••••15",
    description: "Refund to [EMAIL] for ACC-••01",
    email: "••••••••",
    note: "call [PHONE]",
  });
  assert.equal(maskRecord(row, false).origin_name, "Synthetic Customer 011");
  assert.equal(maskRecord(row, false).email, "[EMAIL]");
  assert.equal(maskName(""), "");
});
