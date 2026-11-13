import { pointPhotosForRun } from '../lib/pageTemplates/assemble.js';

// Offline fixture prose only. Production writes every part with the provider;
// these examples never enter its seller-feature dictionary or generation path.
const PART_EXAMPLES = new Map([
  ['단추', '단추가 더해진 여밈으로\n차분한 옷차림을 완성해요'],
  ['목선', '목 둘레를 따라 이어진 선으로\n상의의 윤곽이 깔끔해요'],
  ['라운드 넥', '둥근 곡선으로 이어진 목선이라\n옷차림에 부드러운 인상을 더해요'],
  ['브이넥', '브이 모양으로 내려오는 목선이라\n상의 앞면에 각진 선을 더해요'],
  ['소맷단', '소매 끝에 더한 마감으로\n손목 둘레의 선이 단정해요'],
  ['소매 커프스', '소매 끝을 나누는 커프스로\n손목 둘레의 윤곽이 또렷해요'],
  ['밑단', '옷의 끝선을 잇는 밑단 마감으로\n전체 윤곽을 단정하게 정리해요'],
  ['골지 원단', '골이 반복되는 원단 조직으로\n가까이서 볼수록 표면이 또렷해요'],
  ['어깨선', '몸판과 소매를 잇는 어깨선으로\n상체의 윤곽을 또렷하게 정리해요'],
  ['드롭 숄더', '어깨 아래로 내려온 절개라\n상체 윤곽이 한결 느긋해요'],
  ['기장', '상의 아래로 이어지는 끝선으로\n겹쳐 입을 때 길이 차가 드러나요'],
  ['목선이 앉는 모양', '목 둘레에 자리 잡은 마감선으로\n얼굴 아래의 윤곽을 정리해요'],
  ['입었을 때 기장', '끝선까지 이어지는 옷의 길이로\n함께 입은 옷과 균형을 잡아요'],
  ['입었을 때 실루엣', '옷의 바깥선을 이루는 윤곽이라\n함께 입는 옷과 균형을 맞춰요'],
  ['입었을 때 허리선', '허리 둘레를 따라 이어진 선으로\n상의와 하의의 경계를 정리해요'],
  ['옆 포켓', '옆선에 자리 잡은 포켓으로\n옆모습에 작은 포인트를 더해요'],
  ['배색 스티치', '바탕과 색이 다른 스티치로\n봉제선의 윤곽이 선명해요'],
  ['롤업 소매', '접어 올린 소매 마감으로\n손목이 드러나 산뜻해요'],
  ['언밸런스 햄라인', '앞뒤 길이가 다른 밑단으로\n옆에서 본 옷의 선이 경쾌해요'],
]);

// The mock generation endpoints persist the same split field as the worker.
// Keep this out of skeleton/preview assembly, which must never create copy.
export function withMockPartDescriptions(storyboard, analysis, copywriting) {
  if (!copywriting) return analysis;
  const candidates = analysis.detailRecommendations?.candidates || [];
  const runs = [];
  for (const card of storyboard) {
    if (runs.at(-1)?.[0].pageSectionKey !== card.pageSectionKey) runs.push([]);
    runs.at(-1).push(card);
  }
  const labels = new Set();
  for (const run of runs) {
    if (run[0].pageSectionKind !== 'points') continue;
    for (const card of pointPhotosForRun(run, storyboard)) {
      const label = card.pointItem?.kind === 'wornCrop' ? card.pointItem.label
        : card.detailTargetId != null ? candidates.find(item => String(item.id) === String(card.detailTargetId))?.label : null;
      if (typeof label === 'string' && label.trim()) labels.add(label);
    }
  }
  if (!labels.size) return analysis;
  const merged = new Map((analysis.partDescriptionCopy || [])
    .filter(item => typeof item?.point === 'string' && item.desc).map(item => [item.point, item.desc]));
  for (const label of labels) {
    const desc = PART_EXAMPLES.get(label);
    if (desc) merged.set(label, desc);
  }
  return { ...analysis, partDescriptionCopy: [...merged].map(([point, desc]) => ({ point, desc })) };
}
