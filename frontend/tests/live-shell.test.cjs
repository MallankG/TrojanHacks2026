/* eslint-disable @typescript-eslint/no-require-imports -- Node CommonJS regression harness. */
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function render(live) {
  const source = fs.readFileSync(path.join(__dirname, '../src/components/shell/app-shell.tsx'), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  const noop = () => null;
  const mock = {
    'next/navigation': { usePathname: () => '/graph', useRouter: () => ({ replace: noop }) },
    '@/lib/live/session': { useLiveProfile: () => live ? { id: 'live-user' } : null },
    '@/lib/routes': { ROUTES: {}, screenForPath: () => 'graph' },
    '@/lib/store/actions': { markVisited: noop, signOut: noop, toast: noop },
    '@/lib/store/hooks': { useDemoState: () => ({ authed: true }), useHydrated: () => true },
    '@/components/ui/misc': { Skeleton: noop },
    '@/components/modals/modal-host': { ModalHost: noop },
    './banners': { Banners: () => React.createElement('aside', null, 'seeded data hold'), CaseContextBar: () => React.createElement('aside', null, 'CASE-0412') },
    './sidebar': { Sidebar: noop }, './topbar': { Topbar: noop },
  };
  new Function('require', 'module', 'exports', js)((id) => mock[id] ?? require(id), module, module.exports);
  return renderToStaticMarkup(React.createElement(module.exports.AppShell, null, React.createElement('h1', null, 'Live graph')));
}

test('live investigation never displays seeded case or data-hold context', () => {
  assert.match(render(true), /Live graph/);
  assert.doesNotMatch(render(true), /CASE-0412|seeded data hold/);
  assert.match(render(false), /CASE-0412/);
  assert.match(render(false), /seeded data hold/);
});
