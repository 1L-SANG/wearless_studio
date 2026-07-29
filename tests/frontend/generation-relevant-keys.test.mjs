// src/store/useAppStore.js is a Vite-only module (uses the `@/` alias, unattributed
// JSON imports, and `import.meta.env`) so it can't be imported by plain `node --test`
// the way the rest of this suite's pure src/lib modules are. The hooks below are
// generic, file-agnostic compatibility shims — not stand-ins for business logic —
// that let this test load and exercise the REAL store module:
//   - `@/…` → resolved to `src/…` (mirrors vite.config.js's `resolve.alias`)
//   - `*.json` imports missing the `type: 'json'` attribute get it added
//   - `import.meta.env` (absent in plain Node) is polyfilled to `{}`, which is
//     exactly what an unset Vite env looks like — the store's own `?? 'mock'`
//     fallback already assumes this.
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

const SRC_DIR = pathToFileURL(path.resolve(import.meta.dirname, '../../src') + '/').href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('@/')) return nextResolve(SRC_DIR + specifier.slice(2), context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.json') && context.importAttributes?.type !== 'json') {
      return nextLoad(url, { ...context, importAttributes: { ...context.importAttributes, type: 'json' } });
    }
    const result = nextLoad(url, context);
    if (result.source && result.format === 'module') {
      const text = result.source.toString();
      if (text.includes('import.meta.env')) {
        return { ...result, source: text.replaceAll('import.meta.env', '(globalThis.__ENV_SHIM__ ??= {})') };
      }
    }
    return result;
  },
});

const { isGenerationRelevantAnalysisPatch } = await import(
  pathToFileURL(path.resolve(import.meta.dirname, '../../src/store/useAppStore.js')).href
);

test('mannequinBody patch is generation-relevant — regression guard for the missed refresh trigger', () => {
  assert.equal(
    isGenerationRelevantAnalysisPatch({ mannequinBody: { bust: 'volume', hip: 'regular' } }),
    true,
  );
});

test('a patch touching only a non-generation-relevant key is not flagged', () => {
  assert.equal(isGenerationRelevantAnalysisPatch({ suggestedName: 'x' }), false);
});

test('a representative pre-existing generation-relevant key still returns true', () => {
  assert.equal(isGenerationRelevantAnalysisPatch({ fitProfile: { category: 'top' } }), true);
});

test('null, undefined and empty-object patches are never generation-relevant', () => {
  assert.equal(isGenerationRelevantAnalysisPatch(null), false);
  assert.equal(isGenerationRelevantAnalysisPatch(undefined), false);
  assert.equal(isGenerationRelevantAnalysisPatch({}), false);
});
