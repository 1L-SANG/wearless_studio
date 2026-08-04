export const MANNEQUIN_EDIT_OPTIONS = Object.freeze([
  Object.freeze({
    id: 'garment_length',
    label: '옷 길이',
    negativeLabel: '더 짧게',
    positiveLabel: '더 길게',
    editType: 'GARMENT_LENGTH_ONLY',
    adjustmentKey: 'garmentLengthStep',
  }),
  Object.freeze({
    id: 'sleeve_length',
    label: '소매 길이',
    negativeLabel: '더 짧게',
    positiveLabel: '더 길게',
    editType: 'SLEEVE_LENGTH_ONLY',
    adjustmentKey: 'sleeveLengthStep',
  }),
  Object.freeze({
    id: 'body_width',
    label: '몸통 폭',
    negativeLabel: '더 슬림하게',
    positiveLabel: '더 여유롭게',
    editType: 'BODY_WIDTH_ONLY',
    adjustmentKey: 'bodyWidthStep',
  }),
  Object.freeze({
    id: 'shoulder_width',
    label: '어깨 폭',
    negativeLabel: '더 좁게',
    positiveLabel: '더 넓게',
    editType: 'SHOULDER_WIDTH_ONLY',
    adjustmentKey: 'shoulderWidthStep',
  }),
  Object.freeze({
    id: 'tuck_state',
    label: '넣어 입기',
    negativeLabel: '밖으로 빼기',
    positiveLabel: '안으로 넣기',
    editType: 'TUCK_STATE_ONLY',
    adjustmentKey: 'tuckStateStep',
  }),
  Object.freeze({
    id: 'mannequin_volume',
    label: '마네킹 볼륨',
    negativeLabel: '더 슬림하게',
    positiveLabel: '더 볼륨 있게',
    editType: 'MANNEQUIN_VOLUME_ONLY',
    adjustmentKey: 'mannequinVolumeStep',
  }),
]);

const EDIT_DEFINITIONS = Object.freeze(Object.fromEntries(
  MANNEQUIN_EDIT_OPTIONS.map((option) => [option.id, option]),
));

const ALLOWED_STEPS = new Set([-2, -1, 1, 2]);

export function buildMannequinEditRequest(kind, step) {
  const definition = EDIT_DEFINITIONS[kind];
  if (!definition) throw new Error('지원하지 않는 AI 조정 항목이에요.');
  if (!ALLOWED_STEPS.has(step)) throw new Error('조정 강도를 다시 선택해 주세요.');
  return {
    editType: definition.editType,
    adjustments: { [definition.adjustmentKey]: step },
  };
}

export async function runMannequinEdit({
  api,
  projectId,
  cutId,
  kind,
  step,
  idempotencyKey,
  onProgress,
}) {
  if (!projectId || !cutId) throw new Error('조정할 마네킹컷을 선택해 주세요.');
  const request = buildMannequinEditRequest(kind, step);
  const baseline = await api.approveMannequin(projectId, cutId);
  return api.editMannequin(projectId, {
    ...request,
    baselineId: baseline?.id || undefined,
    idempotencyKey,
    onProgress,
  });
}

function errorCode(error) {
  return error?.code
    || error?.response?.detail?.code
    || error?.detail?.code
    || error?.errorCode
    || '';
}

export function mannequinEditFailureMessage(error) {
  switch (errorCode(error)) {
    case 'no_approved_baseline':
      return '먼저 현재 마네킹컷을 승인한 뒤 다시 시도해 주세요.';
    case 'baseline_changed':
      return '승인 기준이 바뀌었어요. 현재 컷을 다시 선택한 뒤 부분 수정을 실행해 주세요.';
    case 'job_in_progress':
      return '이미 진행 중인 마네킹 작업이 있어요. 완료 후 다시 시도해 주세요.';
    case 'idempotency_conflict':
      return '같은 요청 키로 다른 수정을 보낼 수 없어요. 다시 눌러 주세요.';
    case 'edit_not_enabled':
      return 'AI 부분 수정 기능이 아직 켜져 있지 않아요.';
    case 'misconfigured_feature':
      return 'AI 부분 수정 설정이 완전하지 않아요. 서버 설정을 확인해 주세요.';
    case 'insufficient_credits':
      return '크레딧이 부족해요.';
    default:
      return error?.message || 'AI 부분 수정에 실패했어요. 현재 컷은 그대로 유지됩니다.';
  }
}
