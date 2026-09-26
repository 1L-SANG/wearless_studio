/* 완성됐지만 아직 확정 주소가 없는 컷의 타일 내용(2026-09-26) — 순수 표시 컴포넌트.
   미리보기(objectURL)가 있으면 그림을, 없으면(받는 중·거절·없음) '완성됐어요'를 보인다.
   자리는 여전히 잠겨 있다(대기 타일) — 완료 병합이 안정 주소로 바꾼 뒤에야 편집할 수 있다.
   무거운 임포트가 없어 node 테스트에서 renderToStaticMarkup 으로 바로 그린다. */
export function GenDoneTileBody({ previewUrl }) {
  if (previewUrl) {
    return (
      <>
        <img className="ed-genwait-preview" src={previewUrl} alt="" draggable={false} />
        <span className="ed-genwait-hint">완성됐어요 · 상세페이지가 마무리되면 편집할 수 있어요</span>
      </>
    );
  }
  return (
    <span className="ed-genwait-done">완성됐어요<br /><small>상세페이지가 마무리되면 여기에 보여요</small></span>
  );
}
