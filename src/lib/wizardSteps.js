/* 제작 마법사의 단계 정의 — 마네킹 생성이 오래 걸려 콘티보다 뒤에 온다. 사용자는 콘티를 짜는
   동안 생성이 백그라운드로 돌게 두고, 마네킹 화면에서 결과를 확인한다.
   shell.jsx(React 의존) 밖에 두어 node --test 로 순서 회귀를 잡는다. */
export const WIZARD_STEPS = [
  { key: 'input', label: '제품 정보·분석' },
  { key: 'storyboard', label: '콘티보드' },
  { key: 'mannequin', label: '마네킹컷' },
  { key: 'editor', label: '에디터' },
];

/* input+analysis 는 0번으로 합치고, generating 은 editor 단계를 공유한다. */
export const STEP_INDEX = {
  input: 0,
  analysis: 0,
  storyboard: 1,
  mannequin: 2,
  generating: 3,
  editor: 3,
};
