import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
test('the requested set panel takes precedence over a selected similar-cut panel', async () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('drawer && <StoryboardDrawer'), end = source.indexOf('</StoryboardDrawer>}', start) + '</StoryboardDrawer>'.length;
  const { code } = await transformWithEsbuild(`return (${source.slice(start, end)});`, 'drawer-choice.jsx', { jsx: 'transform' });
  const values = { React: { createElement: (type, props, ...children) => ({ type, props, children }) }, drawer: { mode: 'set' },
    StoryboardDrawer: 'Drawer', StoryboardSpaceSetPanel: 'Sets', selected: { id: 'child', similarSourceBlockId: 'root' },
    setPicker: { mode: 'add' }, pickerMembers: [], pickerRun: null, blocks: [] };
  const scope = new Proxy(values, { has: () => true, get: (target, key) => key === Symbol.unscopables ? undefined : target[key] });
  const tree = new Function('scope', `with(scope) { ${code} }`)(scope);
  assert.equal(tree.type, 'Drawer'); assert.equal(tree.children[0].type, 'Sets'); assert.equal(tree.children[0].props.mode, 'add');
});
