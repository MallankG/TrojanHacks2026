/* eslint-disable @typescript-eslint/no-require-imports -- Node CommonJS regression harness. */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../src/lib/live/api-graph.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
test("live canvas fetches canonical engine and preserves workbench filters and shape", async () => {
  const requests = [];
  const graph = { nodes: [{ id: "a" }], edges: [], truncated: false };
  const mod = { exports: {} };
  const api = { call: async (url, options) => { requests.push({ url, options }); return { workbench_graph: graph }; }, query: () => "" };
  new Function("require", "module", "exports", compiled)((name) => { assert.equal(name, "./api"); return api; }, mod, mod.exports);
  const result = await mod.exports.graphApi.neighborhood("a", { hops: 2, direction: "incoming", rails: ["wire"], minAmount: 150, start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" });
  assert.equal(result, graph);
  assert.equal(requests[0].url, "/graph/engine/analysis");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(requests[0].options.body, { operation: "neighborhood", account_id: "a", hops: 2, graph_direction: "incoming", channels: ["wire"], min_amount: 150, start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" });
});
