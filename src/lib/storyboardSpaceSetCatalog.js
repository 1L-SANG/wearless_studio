/*
 * 공간 세트 파일럿 카탈로그.
 *
 * 실제 세트 자산이 준비되면 이 파일의 데이터만 같은 shape로 교체한다.
 * 보드/인스펙터는 공간 이름과 구성만 소비하고 서버 저장 계약은 기존
 * StoryboardBlock.spaceGroupId 공유 방식 그대로 유지한다.
 */
export const STORYBOARD_SPACE_SETS = Object.freeze([
  Object.freeze({
    id: 'cafe', name: '햇살 카페', place: '카페', compositionLabel: '풀+미디움', tone: 'cafe',
    members: Object.freeze([
      Object.freeze({ cutType: 'styling', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'styling', direction: 'side', shot: 'medium' }),
    ]),
  }),
  Object.freeze({
    id: 'street', name: '도심 거리', place: '거리', compositionLabel: '풀+미디움', tone: 'street',
    members: Object.freeze([
      Object.freeze({ cutType: 'styling', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'styling', direction: 'side', shot: 'medium' }),
    ]),
  }),
  Object.freeze({
    id: 'home', name: '포근한 집', place: '집', compositionLabel: '풀+미디움', tone: 'home',
    members: Object.freeze([
      Object.freeze({ cutType: 'styling', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'styling', direction: 'side', shot: 'medium' }),
    ]),
  }),
  Object.freeze({
    id: 'beach', name: '잔잔한 해변', place: '해변', compositionLabel: '풀+미디움', tone: 'beach',
    members: Object.freeze([
      Object.freeze({ cutType: 'styling', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'styling', direction: 'side', shot: 'medium' }),
    ]),
  }),
  Object.freeze({
    id: 'garden', name: '초록 정원', place: '정원', compositionLabel: '풀+미디움', tone: 'garden',
    members: Object.freeze([
      Object.freeze({ cutType: 'styling', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'styling', direction: 'side', shot: 'medium' }),
    ]),
  }),
  Object.freeze({
    id: 'studio', name: '화이트 스튜디오', place: '스튜디오', compositionLabel: '회전 풀×3', tone: 'studio',
    members: Object.freeze([
      Object.freeze({ cutType: 'horizon', direction: 'front', shot: 'full' }),
      Object.freeze({ cutType: 'horizon', direction: 'side', shot: 'full' }),
      Object.freeze({ cutType: 'horizon', direction: 'back', shot: 'full' }),
    ]),
  }),
]);

const SET_BY_ID = new Map(STORYBOARD_SPACE_SETS.map((set) => [set.id, set]));
const GROUP_PREFIX = 'sgset__';

export function storyboardSpaceSetById(id) {
  return SET_BY_ID.get(id) || null;
}

export function spaceSetGroupId(setId, uniqueId) {
  return `${GROUP_PREFIX}${setId}__${uniqueId}`;
}

export function spaceSetIdFromGroupId(groupId) {
  if (typeof groupId !== 'string' || !groupId.startsWith(GROUP_PREFIX)) return null;
  const id = groupId.slice(GROUP_PREFIX.length).split('__')[0];
  return SET_BY_ID.has(id) ? id : null;
}

export function inferStoryboardSpaceSet(groupId, members = []) {
  const storedId = spaceSetIdFromGroupId(groupId);
  if (storedId) return storyboardSpaceSetById(storedId);
  const looksLikeStudio = members.length >= 3
    && members.every((block) => block?.cutType === 'horizon' && block?.shot === 'full');
  return storyboardSpaceSetById(looksLikeStudio ? 'studio' : 'cafe');
}
