const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { renderToStaticMarkup } = require("react-dom/server");
const React = require("react");
const source = fs.readFileSync(path.join(__dirname, "../src/features/live/alert-explanation.tsx"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const mod = { exports: {} };
new Function("require", "module", "exports", compiled)(require, mod, mod.exports);
const render = (explanation) => renderToStaticMarkup(React.createElement(mod.exports.AlertExplanation, { explanation }));
test("renders structured detector explanations without rendering objects", () => {
  const html = render({ summary: "Cycle lead", reasons: ["Three hops", "Returned funds"], score_breakdown: { typology: 0.5 } });
  assert.match(html, /Cycle lead/);
  assert.match(html, /<li>Three hops<\/li>/);
  assert.match(html, /Returned funds/);
});
test("supports legacy text, empty and malformed reasons", () => {
  assert.match(render("Legacy lead"), /Legacy lead/);
  assert.equal(render(null), "");
  assert.equal(render({}), "");
  assert.doesNotThrow(() => render({ reasons: [null, { unsafe: true }, "safe"] }));
});
