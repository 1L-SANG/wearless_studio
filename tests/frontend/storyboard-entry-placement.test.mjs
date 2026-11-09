import test from 'node:test';
import assert from 'node:assert/strict';
import { pickEntrySets, entryStylingMembers, seededPick, normalizePlaceType, hasFullAndMediumMembers } from '../../src/lib/storyboardEntryPlacement.js';
import { componentModule } from './helpers/reactHarness.mjs';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { isStoryboardSpaceSetEligible } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { seedStoryboard, isUntouchedTemplateStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { signatureCutMatches } from '../../src/lib/signatureCutPool.js';
for(const [gender,clothingType] of [['women','top'],['women','bottom'],['women','outer'],['women','dress'],['men','top'],['men','bottom'],['men','outer']]) {
 for(const templateId of ['emo','show']) test(`${templateId}/${gender}/${clothingType} uses eligible entry sets and the showroom signature pool`,()=>{
 const a={targetGenders:[gender],pageStyle:{...defaultPageStyle(),templateId}};
 const product={clothingType,colors:[{id:'base',isBase:true}]};
 const seeded=seedStoryboard(product,a,{projectId:'entry'});
 const b=assignGenerationExamples(seeded,{catalog:[],product,gender,projectId:'entry'}).blocks;
 const sets=pickEntrySets({gender,clothingType,projectId:'entry',stylingCount:3});
 assert.ok(sets.rotationSet);
 assert.ok(b.filter(b=>b.spaceGroupId).every(b=>spaceSetIdFromGroupId(b.spaceGroupId)&&b.exampleId&&b.refScope===(b.cutType==='horizon'?'all':'pose')));
 const album=b.filter(b=>b.pageSectionKey===`${templateId}.wear_styling`);assert.equal(album.length,6);
 assert.ok(album.every(b=>b.pageSectionUsage===(templateId==='show'?'fit':'styling')));
 const rotation=b.filter(b=>b.pageSectionKey===`${templateId}.wear_fit`);assert.equal(rotation.length,3);
 assert.deepEqual(rotation.map(b=>b.direction),['front','side','back']);
 assert.ok(!album.some(s=>rotation.some(f=>s.spaceGroupId&&f.spaceGroupId===s.spaceGroupId)));
 if(templateId==='show') {
  const first=b[0];
  assert.ok(signatureCutMatches(first.exampleId,{gender,clothingType}));
  assert.equal(first.cutType,'horizon');assert.equal(first.shot,'medium');assert.equal(first.direction,'front');
  assert.equal(first.refScope,'all');assert.ok(!first.spaceGroupId);
  assert.equal(first.pageDesignId,seeded[0].pageDesignId);
  assert.equal(isUntouchedTemplateStoryboard(b),true);
 }
 });
}
test('entry picks are stable for a project and styling members use at most two published poses',()=>{
 const args={gender:'women',clothingType:'top',projectId:'entry',stylingCount:3};
 assert.deepEqual(pickEntrySets(args),pickEntrySets(args));
 for(const set of pickEntrySets(args).stylingSets.filter(Boolean))assert.ok(entryStylingMembers(set).length<=2);
 assert.equal(seededPick([], 'entry'),null);
});

test('women bottom soft preference falls back to all-full sets to preserve a fourth place', async () => {
  // #608 catalog corrections must not remove coverage of a fourth all-full place.
  const sets = ['cafe', 'home', 'nature', 'coast'].map((placeType, index) => ({
    id: `fallback-${placeType}`, setType: 'styling', placeType, gender: 'women',
    applicableClothingTypes: ['bottom'], setApplicableClothingTypes: ['bottom'],
    members: [
      { order: 1, exampleId: `${placeType}-full`, shot: 'full', direction: 'front' },
      { order: 2, exampleId: `${placeType}-second`, shot: index === 3 ? 'full' : 'medium', direction: 'back' },
    ],
  }));
  const { pickEntrySets: pickFixtureSets } = await componentModule('src/lib/storyboardEntryPlacement.js', ['pickEntrySets'], {
    filterSpaceSetsForModel,
    storyboardSpaceSetsFor: options => sets.filter(set => isStoryboardSpaceSetEligible(set, options)),
  });
  const picked = pickFixtureSets({ gender: 'women', clothingType: 'bottom', projectId: 'soft-place-fallback', stylingCount: 4 }).stylingSets;
  const places = picked.map(set => normalizePlaceType(set.placeType, set.setType));
  assert.equal(picked.filter(Boolean).length, 4);
  assert.equal(new Set(places).size, 4);
  assert.equal(picked.filter(hasFullAndMediumMembers).length, 3);
  assert.equal(picked[3].id, 'fallback-coast');
});
