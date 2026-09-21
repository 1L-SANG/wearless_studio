/* 방향 칩(화면) ↔ 컷 스펙(서버) 변환.
 *
 * 서버 계약은 front/side/back 셋 그대로다(server/app/agents/cut_generator._DIRECTIONS).
 * 화면에서만 '사이드'를 둘로 갈라 보여준다 — 같은 side 주문이 두 가지 다른 그림이 되기
 * 때문이다.
 *
 *   사선(threeQuarter)  몸만 옆, 얼굴은 카메라 쪽 3/4, 두 눈 다 보임
 *                       → sideStyle=threeQuarter 가 각도 교체를 건너뛰게 하고
 *                         프롬프트가 DIR:side_identity 로 간다. 얼굴은 LoRA 가 그린다.
 *   옆모습(profile)     완전 옆모습, 한 눈만
 *                       → 각도 교체(ComfyUI)가 등록 실사진 머리를 통째로 붙인다.
 *
 * ★ 변환을 여기 한 곳에만 둔다. 콘티보드·에디터 두 화면이 같은 규칙을 써야 하고,
 *   갈리면 한쪽에서 고른 값이 다른 쪽에서 다른 컷이 된다.
 *
 * ★ 좌/우는 셀러가 고르지 않는다. 서버가 베이스 컷에서 코 방향을 검출해
 *   등록 사진을 고른다(face_angle_swap.plan 의 nose_right). 화면 값은 4개뿐이다.
 */

export const DIRECTION_CHOICES = Object.freeze(['front', 'threeQuarter', 'profile', 'back']);

/** 화면 값 → 컷 스펙 조각. 모르는 값은 정면으로 떨어뜨린다(조용히 사선으로 새지 않게). */
export function specFromDirectionChoice(choice) {
  switch (choice) {
    case 'threeQuarter':
      return { direction: 'side', sideStyle: 'threeQuarter' };
    case 'profile':
      return { direction: 'side', sideStyle: 'profile' };
    case 'back':
      return { direction: 'back', sideStyle: null };
    default:
      return { direction: 'front', sideStyle: null };
  }
}

/** 컷 스펙 → 화면 값. side 인데 sideStyle 이 없으면 옆모습으로 본다(서버 기본값과 같다). */
export function directionChoiceFromSpec(block) {
  const direction = block && block.direction;
  if (direction === 'back') return 'back';
  if (direction !== 'side') return 'front';
  return (block && block.sideStyle) === 'threeQuarter' ? 'threeQuarter' : 'profile';
}
