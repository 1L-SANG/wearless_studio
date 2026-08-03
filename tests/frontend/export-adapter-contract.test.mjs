import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const adapter = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);
const editor = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);

test('exportProject posts snapshot hash, body, options and polls export job', () => {
  assert.match(adapter, /async exportProject\(projectId, \{ snapshot, body = \{\}, options = \{\}, onProgress, key \} = \{\}\)/);
  assert.match(adapter, /const snapshotHash = await sha256Hex\(snapshot \|\| \{\}\)/);
  assert.match(adapter, /\/v1\/projects\/\$\{projectId\}\/export/);
  assert.match(adapter, /body: \{ snapshot: snapshot \|\| \{\}, snapshotHash, body, options \}/);
  assert.match(adapter, /idempotencyKey: key \|\| newIdempotencyKey\(\)/);
  assert.match(adapter, /const result = await pollJob\(res\.jobId/);
  assert.match(adapter, /return \{ \.\.\.result, jobId: res\.jobId/);
});

test('editor saves the authoritative snapshot before export and downloads the artifact', () => {
  assert.match(editor, /const current = latestBlocks\.current \|\| blocks/);
  assert.match(editor, /await api\.saveEditorBlocks\(projectId, current\)/);
  assert.match(editor, /snapshot: \{ editorBlocks: current \}/);
  assert.match(editor, /body: \{ title: productName \}/);
  assert.match(editor, /await api\.exportProject\(projectId/);
  assert.match(editor, /a\.href = out\.src/);
  assert.match(editor, /disabled=\{exporting\}/);
  assert.doesNotMatch(editor, /exportBusy/);
  assert.equal((editor.match(/const exportDetailPage = async/g) || []).length, 1);
});
