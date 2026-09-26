import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

// 실제 AnalysisProgress 소스를 잘라 렌더한다. 진행 막대 계산은 이 검사의 관심사가 아니라 고정값을 준다.
const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const body = source.slice(source.indexOf('const ANALYZE_STEPS'), source.indexOf('export function AnalysisSkeleton'))
  .replace('export function AnalysisProgress', 'function AnalysisProgress');
const { code } = await transformWithEsbuild(body, 'progress.jsx', { jsx: 'transform' });
const AnalysisProgress = new Function('React', 'useState', 'useRef', 'useEffect', 'useSteppedProgress',
  `${code};return AnalysisProgress;`)(React, React.useState, React.useRef, React.useEffect, () => 0);

test('multi-color analysis shows a short wait note only when other colors are sent', () => {
  const note = '색상이 여러 개라 분석이 조금 더 걸릴 수 있어요.';
  assert.match(renderToStaticMarkup(React.createElement(AnalysisProgress, { done: false, colorNote: true })), new RegExp(note));
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(AnalysisProgress, { done: false })), new RegExp(note));
});
