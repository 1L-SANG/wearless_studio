import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';

test('requesting a set gallery overrides a selected similar-cut inspector', async () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('  const inspector =');
  const end = source.indexOf('  const selectedOrdinal =', start);
  const { code } = await transformWithEsbuild(source.slice(start, end) + '\nreturn inspector;', 'inspector.jsx', { loader: 'jsx' });
  const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
  const values = { selected: { id: 'child', similarSourceBlockId: 'root' }, similarSource: null,
    setPicker: { mode: 'add' }, React, Icon: 'Icon', SpaceSetGallery: 'SpaceSetGallery',
    catalogs: {}, catalogContext: { catalog: [] } };
  // Unused event handlers remain inert; evaluate the actual render decision.
  const scope = new Proxy(values, {
    has: (_, key) => key !== 'inspector',
    get: (target, key) => key === Symbol.unscopables ? undefined : target[key],
  });
  const rendered = new Function('scope', `with (scope) { ${code} }`)(scope);
  assert.equal(rendered.type, 'SpaceSetGallery');
  assert.equal(rendered.props.mode, 'add');
});
