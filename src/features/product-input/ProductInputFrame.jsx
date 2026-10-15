import { PageHead } from '@/features/shell/shell.jsx';

export function ProductInputHeading({ mobile }) {
  return (
    <PageHead
      title="의류 이미지를 올려주세요"
      sub={mobile ? '사진만 올리면 PC에서 이어서 만들어요.'
        : <>사진 몇 장만으로 경험해보세요.<br />부족한 정보는 AI 분석 후 직접 확인하고 보정할 수 있어요.</>}
    />
  );
}

export function ProductInputPlaceholder({ mobile, children }) {
  return (
    <div className={`wizard${mobile ? ' pi-handoff' : ''}`} aria-busy="true" aria-label="입력 화면 불러오는 중">
      {children}
      <ProductInputHeading mobile={mobile} />
      {/* 빈 입력 카드의 패딩·4개 사진 격자로 크기만 확보한다. 안쪽은 그리지 않는다. */}
      <div className="merged-card product-input-placeholder-card" aria-hidden="true">
        <div className="surface">
          <div className="sec-head"><div className="sec-title pi-placeholder-line" /></div>
          <div className="sec-sub pi-placeholder-name-help pi-placeholder-line" />
          <div className="field pi-placeholder-line" />
        </div>
        <div className="surface pi-images">
          <div className="sec-head"><div className="sec-title pi-placeholder-line" /><div className="pill pill-soft pi-placeholder-line" /></div>
          <div className="sec-sub pi-placeholder-photo-help pi-placeholder-line" />
          <div className="color-group">
            <div className="color-bar"><div className="color-ind-label pi-placeholder-line" /></div>
            <div className="slot-wells">
              {Array.from({ length: 4 }, (_, index) => (
                <div className="slot-well" key={index}>
                  <div className="slot-well-head pi-placeholder-line" />
                  <div className="slot-tiles"><div className="tile" /></div>
                </div>
              ))}
            </div>
          </div>
          <div className="pi-placeholder-add-color" />
        </div>
      </div>
    </div>
  );
}
