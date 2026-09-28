/* 완성됐지만 아직 확정 주소가 없는 컷의 타일 내용(2026-09-26) — 순수 표시 컴포넌트.
   미리보기(objectURL)가 있으면 그림을, 없으면(받는 중·거절·없음) '완성됐어요'를 보인다.
   자리는 여전히 잠겨 있다(대기 타일) — 완료 병합이 안정 주소로 바꾼 뒤에야 편집할 수 있다.
   무거운 임포트가 없어 node 테스트에서 renderToStaticMarkup 으로 바로 그린다. */
/* retained(2026-09-28, 부분 실패 뒤): 'kept' = 보관본이 있어 다시 만들 때 그대로 쓴다,
   'lost' = 완성은 했지만 보관되지 않아(또는 기한이 지나) 다시 만들 때 새로 그린다. null = 생성 중·완료. */
const RETAINED_NOTE = {
  kept: '보관 중 · 다시 만들 때 그대로 써요',
  lost: '보관되지 않아 다시 만들 때 새로 만들어요',
};

export function GenDoneTileBody({ previewUrl, retained = null }) {
  const note = RETAINED_NOTE[retained] || null;
  if (previewUrl) {
    return (
      <>
        <img className="ed-genwait-preview" src={previewUrl} alt="" draggable={false} />
        <span className="ed-genwait-hint">
          {note ? `완성됐어요 · ${note}` : '완성됐어요 · 상세페이지가 마무리되면 편집할 수 있어요'}
        </span>
      </>
    );
  }
  return (
    <span className="ed-genwait-done">완성됐어요<br /><small>{note || '상세페이지가 마무리되면 여기에 보여요'}</small></span>
  );
}
