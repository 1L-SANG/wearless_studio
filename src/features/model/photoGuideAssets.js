const base = '/models/registration/clay-ponytail-v1';

// 사진 번호 대신 슬롯 이름으로 연결해 기존 업로드 계약과 같은 항목을 보여줘요.
// 생성본 가운데 줄의 시선은 왼쪽 칸이 화면 오른쪽, 오른쪽 칸이 화면 왼쪽을 향해요.
// 실제 눈동자를 확인한 순서로 연결하며, 얼굴 전체를 뒤집지 않아요.
const shadeSlots = [
  'sh_front', 'sh_smile', 'sh_34',
  'sh_front2', 'sh_gaze_right', 'sh_gaze_left',
  'sh_side', 'sh_side_right', 'sh_back',
];
const sunlightSlots = [
  'sl_front', 'sl_smile', 'sl_34',
  'sr_front', 'sr_smile', 'sr_34',
  'bl_front', 'bl_smile', 'bl_34',
];

export const PHOTO_GUIDE_ASSETS = Object.freeze(Object.fromEntries(
  [[shadeSlots, 'shade.jpg'], [sunlightSlots, 'sunlight-shadow-v2.jpg']].flatMap(([slots, file]) =>
    slots.map((key, index) => [key, Object.freeze({ src: `${base}/${file}`, column: index % 3, row: Math.floor(index / 3) })]),
  ),
));
