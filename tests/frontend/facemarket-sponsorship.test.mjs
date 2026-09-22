import test from 'node:test';
import assert from 'node:assert/strict';
import { sponsorshipDraft, sponsorshipPayload, formatFollowers, filterSponsorshipModels } from '../../src/features/model/sponsorshipOptions.js';
import { toBrowseModel, fromExampleModel } from '../../src/features/facemarket-landing/data/publicModels.js';
import { BROWSE_MODELS } from '../../src/features/facemarket-landing/data/browseModels.js';

const enabled = { sponsorshipEnabled: true, instagramHandle: '@daily.style', instagramFollowers: '0', sizeTop: 'M', sizeBottomWaist: '28', profileConsent: true };
test('선택 협찬은 기본 꺼짐이고 끄면 기존 프로필을 지우는 필드를 보내지 않아요', () => {
  assert.equal(sponsorshipDraft().sponsorshipEnabled, false);
  assert.deepEqual(sponsorshipPayload({ ...enabled, sponsorshipEnabled: false }), { sponsorshipEnabled: false });
});
test('공개 계정명과 숫자 입력을 정리하며 팔로워가 없어도 켤 수 있어요', () => {
  assert.deepEqual(sponsorshipPayload(enabled), { ...enabled, instagramHandle: 'daily.style', instagramFollowers: 0, sizeBottomWaist: 28 });
});
test('프로필 정보 수집에 동의하지 않으면 켤 수 없고, 저장된 동의 시각이 있으면 동의한 상태로 시작해요', () => {
  assert.throws(() => sponsorshipPayload({ ...enabled, profileConsent: false }), /프로필 정보 수집/);
  assert.equal(sponsorshipDraft({ sponsorshipEnabled: true, sponsorshipProfileConsentAt: '2026-09-22T00:00:00Z' }).profileConsent, true);
  assert.equal(sponsorshipDraft({ sponsorshipEnabled: true }).profileConsent, false);
});
for (const patch of [{instagramHandle:'https://instagram.com/a'}, {instagramHandle:'a..b'}, {instagramFollowers:''}, {instagramFollowers:'1.5'}, {instagramFollowers:'-1'}, {sizeTop:'XXL'}, {sizeBottomWaist:'23'}]) {
 test(`협찬을 켤 때 유효하지 않은 입력을 거부해요 ${JSON.stringify(patch)}`, () => assert.throws(() => sponsorshipPayload({...enabled,...patch})));
}
test('협찬 필터는 실제 참여 모델만 남기고 기존 순서를 보존해요', () => {
 const models=[{id:'a',kind:'real',sponsorship:{enabled:true}},{id:'b',kind:'example',sponsorship:{enabled:true}},{id:'c',kind:'real',sponsorship:null},{id:'d',kind:'real',sponsorship:{enabled:true}}];
 assert.deepEqual(filterSponsorshipModels(models,true).map(x=>x.id),['a','d']);
 assert.deepEqual(filterSponsorshipModels(models,false),models);
 assert.equal(formatFollowers(1200),'1.2천'); assert.equal(formatFollowers(0),'0');
});
test('공개 어댑터는 참여 중인 모델에만 안전한 SNS와 사이즈 정보를 담아요',()=>{
 const item={id:'m1',displayName:'모델',closeupImageUrl:'/image.webp',...sponsorshipPayload(enabled),instagramFollowersReportedAt:'2026-09-22T00:00:00Z'};
 assert.equal(toBrowseModel(item).sponsorship.instagramUrl,'https://www.instagram.com/daily.style/');
 assert.equal(toBrowseModel({...item,sponsorshipEnabled:false}).sponsorship,null);
 assert.deepEqual(toBrowseModel({id:'m2',displayName:'비로그인',closeupImageUrl:'/image.webp',sponsorshipEnabled:true,instagramHandle:null,instagramFollowers:null,sizeTop:null,sizeBottomWaist:null}).sponsorship,{enabled:true,masked:true});
 assert.equal(toBrowseModel(item).sponsorship.masked,false);
 assert.equal('profileConsent' in toBrowseModel(item).sponsorship,false);
 assert.equal(toBrowseModel({...item,instagramHandle:'javascript:alert(1)'}).sponsorship,null);
 assert.equal(fromExampleModel(BROWSE_MODELS[0]).sponsorship,null);
});
