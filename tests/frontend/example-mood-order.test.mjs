import test from 'node:test';
import assert from 'node:assert/strict';

import genExamples from '../../src/data/genExamples.json' with { type: 'json' };
import {
  EXAMPLE_MOOD_BUCKETS,
  exampleMoodBucket,
  orderExamplesByMood,
} from '../../src/lib/exampleMoodOrder.js';

const bucket = (mood, detailSubject = null, id = 'example') => (
  exampleMoodBucket({ mood, detailSubject, id }).id
);

test('free-text example moods enter the owner-confirmed place buckets by keyword', () => {
  assert.equal(bucket('cafe-exterior phone-snapshot'), 'cafe');
  assert.equal(bucket('home'), 'indoor');
  assert.equal(bucket('handheld urban night travel snapshot'), 'city');
  assert.equal(bucket('sunny riverside park close phone snap'), 'nature');
  assert.equal(bucket('bright-coastal-stairway-travel-candid'), 'resort');
  assert.equal(bucket('warm-autumn-heritage-walk-candid'), 'heritage');
  assert.equal(bucket('plain seamless setting', null, 'unknown-example'), 'other');
});

test('the first matching place bucket wins and detailSubject/id participate', () => {
  assert.equal(bucket('cafe beside an urban street'), 'cafe');
  assert.equal(bucket(null, 'coffee neckline detail'), 'cafe');
  assert.equal(bucket(null, null, 'ex_horizon_women_top_medium_01'), 'indoor');
});

test('mood ordering is deterministic by bucket, then rank, then id', () => {
  const input = [
    { id: 'street', mood: 'urban street', rank: 1 },
    { id: 'cafe-z', mood: 'cafe', rank: 2 },
    { id: 'cafe-b', mood: 'coffee shop', rank: 1 },
    { id: 'cafe-a', mood: 'bakery', rank: 1 },
    { id: 'other', mood: 'plain seamless setting', rank: 1 },
  ];
  const expected = ['cafe-a', 'cafe-b', 'cafe-z', 'street', 'other'];
  assert.deepEqual(orderExamplesByMood(input).map((item) => item.id), expected);
  assert.deepEqual(orderExamplesByMood([...input].reverse()).map((item) => item.id), expected);
});

test('all 71 released free-text moods are exercised', () => {
  const moods = new Set(genExamples.map((example) => example.mood).filter(Boolean));
  assert.equal(moods.size, 71);
});

test('runtime styling and horizon examples keep ordinary other coverage at or below 15%', () => {
  // 문서 정본: documents/genexamples_release_contract.md §7 갤러리 나열 규칙.
  const ordinary = genExamples.filter((example) => (
    ['styling', 'horizon'].includes(example.cutType) && !example.setOnly
  ));
  const otherId = EXAMPLE_MOOD_BUCKETS.at(-1).id;
  const classified = ordinary.map((example) => ({
    id: example.id,
    bucketId: exampleMoodBucket(example).id,
  }));
  const otherIds = classified.filter((example) => example.bucketId === otherId).map((example) => example.id);
  const otherRatio = otherIds.length / classified.length;

  assert.ok(
    otherRatio <= 0.15,
    `‘기타’ 비율 ${(otherRatio * 100).toFixed(1)}% (${otherIds.length}/${classified.length}) > 15%; ids: ${otherIds.join(', ') || '(없음)'}`,
  );
});
