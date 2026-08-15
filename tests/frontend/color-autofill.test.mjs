import test from 'node:test';
import assert from 'node:assert/strict';

import { autofillColorGroups } from '../../src/features/product-input/colorAutofill.js';

const suggestions = [{ colorGroupId: 'base', swatchId: 'blue', colorName: '연청' }];

test('셀러가 선택한 스와치는 AI 제안으로 덮지 않는다', () => {
  const colors = [{ id: 'base', name: '', swatchId: 'navy', images: [] }];
  assert.deepEqual(autofillColorGroups(colors, suggestions), [
    { id: 'base', name: '연청', swatchId: 'navy', images: [] },
  ]);
});

test('셀러가 입력한 세부 색 이름은 AI 제안으로 덮지 않는다', () => {
  const colors = [{ id: 'base', name: '빈티지 인디고', images: [] }];
  assert.deepEqual(autofillColorGroups(colors, suggestions), [
    { id: 'base', name: '빈티지 인디고', swatchId: 'blue', images: [] },
  ]);
});

test('이름과 스와치가 모두 빈 그룹은 둘 다 채운다', () => {
  const colors = [{ id: 'base', name: '', images: [] }];
  assert.deepEqual(autofillColorGroups(colors, suggestions), [
    { id: 'base', name: '연청', swatchId: 'blue', images: [] },
  ]);
});

test('제안에 없는 그룹은 무시하고 원본 배열을 유지한다', () => {
  const colors = [{ id: 'other', name: '', images: [] }];
  assert.equal(autofillColorGroups(colors, suggestions), colors);
});

test('레거시 제안에 colorName이 없으면 스와치 라벨로 폴백한다', () => {
  const colors = [{ id: 'base', name: '', images: [] }];
  assert.deepEqual(autofillColorGroups(
    colors,
    [{ colorGroupId: 'base', swatchId: 'navy' }],
    [{ id: 'navy', label: '네이비', hex: '#1f2a44' }],
  ), [{ id: 'base', name: '네이비', swatchId: 'navy', images: [] }]);
});

