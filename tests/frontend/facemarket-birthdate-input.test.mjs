import test from 'node:test';
import assert from 'node:assert/strict';
import { nextBirthdateSegments, backspaceBirthdateSegments, birthdateFromSegments, isAdultBirthdate } from '../../src/features/model/birthdateInput.js';

const today = new Date(2026, 8, 10);
test('four year digits advance to month without mutating the input', () => {
  const segments = ['', '', ''];
  assert.deepEqual(nextBirthdateSegments(segments, 0, '1999'), { segments: ['1999', '', ''], focusIndex: 1 });
  assert.deepEqual(segments, ['', '', '']);
});
test('two month digits advance to day', () => {
  assert.deepEqual(nextBirthdateSegments(['1999', '0', ''], 1, '04'), { segments: ['1999', '04', ''], focusIndex: 2 });
});
test('day entry keeps focus on day', () => {
  assert.equal(nextBirthdateSegments(['1999', '04', ''], 2, '12').focusIndex, 2);
});
test('non-numeric input is ignored and segment lengths are capped', () => {
  assert.deepEqual(nextBirthdateSegments(['1999', '0', ''], 1, '0a'), { segments: ['1999', '0', ''], focusIndex: 1 });
  assert.equal(nextBirthdateSegments(['', '', ''], 0, '19999').segments[0], '1999');
  assert.deepEqual(nextBirthdateSegments(['1999', '04', '12'], 0, 'abc').segments, ['1999', '04', '12']);
});
test('eight pasted digits populate all segments even from the month field', () => {
  assert.deepEqual(nextBirthdateSegments(['', '', ''], 1, '19990412'), { segments: ['1999', '04', '12'], focusIndex: 2 });
});
test('backspace from an empty segment deletes the previous final digit', () => {
  assert.deepEqual(backspaceBirthdateSegments(['1999', '', ''], 1), { segments: ['199', '', ''], focusIndex: 0 });
  assert.deepEqual(backspaceBirthdateSegments(['1999', '04', ''], 2), { segments: ['1999', '0', ''], focusIndex: 1 });
});
test('backspace within a nonempty segment stays there', () => {
  assert.deepEqual(backspaceBirthdateSegments(['1999', '04', '12'], 2), { segments: ['1999', '04', '1'], focusIndex: 2 });
});
test('only complete segments compose an ISO birthdate', () => {
  assert.equal(birthdateFromSegments(['1999', '04', '12']), '1999-04-12');
  assert.equal(birthdateFromSegments(['1999', '4', '12']), '');
});
test('the nineteenth birthday is inclusive, the previous day is not', () => {
  assert.equal(isAdultBirthdate('2007-09-10', today), true);
  assert.equal(isAdultBirthdate('2007-09-11', today), false);
  assert.equal(isAdultBirthdate('2008-01-01', today), false);
});
test('invalid calendar dates, month and day ranges are rejected', () => {
  for (const value of ['1999-00-12', '1999-13-12', '1999-04-00', '1999-04-32', '1999-04-31', '1999-02-29', '0000-01-01', '1999-4-12']) {
    assert.equal(isAdultBirthdate(value, today), false, value);
  }
  assert.equal(isAdultBirthdate('2000-02-29', today), true);
});
