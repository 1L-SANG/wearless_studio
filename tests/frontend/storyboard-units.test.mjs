import test from 'node:test';
import assert from 'node:assert/strict';
import { cardUnits } from '../../src/features/storyboard/storyboardUnits.js';
test('template cards remain individual storyboard units regardless of retired row fields',()=>{
 const items=['a','b','c'].map(id=>({block:{id,layoutRowId:'old',hookFrameId:'old'},index:1}));
 const units=cardUnits(items);
 assert.deepEqual(units.map(u=>u.kind),['card','card','card']);
 assert.deepEqual(units.flatMap(u=>u.items.map(i=>i.block.id)),['a','b','c']);
});
