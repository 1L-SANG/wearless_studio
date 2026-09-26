import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CREDIT_COSTS,
  crossedBelowDetailPageMinimum,
  detailPageCreditCost,
} from '../../src/lib/limits.js';

test('detail-page credit cost applies the five-cut minimum only to a positive count', () => {
  assert.equal(CREDIT_COSTS.storyboardMinCuts, 5);
  for (const [count, expected] of [
    [null, null], [undefined, null], [-1, 0], [0, 0], [1, 95], [4, 95],
    [5, 95], [6, 114], [10, 190],
  ]) {
    assert.equal(detailPageCreditCost(count), expected, `AI cuts: ${count}`);
  }
});

test('minimum-charge notice fires only when a positive count crosses down from five or more', () => {
  for (const [previous, next, expected] of [
    [5, 4, true], [10, 3, true], [5, 0, false],
    [4, 3, false], [3, 6, false], [6, 5, false],
  ]) {
    assert.equal(crossedBelowDetailPageMinimum(previous, next, 5), expected);
  }
});
