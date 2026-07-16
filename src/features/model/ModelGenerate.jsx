/* =============================================================
   features/model — ⑤⑥⑦ 생성 가능 상태·생성·결과 확인
   (/model/generate)
   READY(canGenerate) 가 아니면 진입을 막고 부족한 항목을 보여준다. 상품
   이미지는 보관함 프로젝트에서 고른다(기존 assets, api-spec §4 골격 —
   productImageAssetIds). 결과는 게이트 URL 로만 표시한다(§1.4).
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Button, ErrorState, Icon, ProgressBar, useToast } from '@/components/ui.jsx';
import { fetchGenerationResultUrl, getStatus, startGeneration } from '@/lib/api/personalization.js';
import s from './ModelPersonalization.module.css';

function blockerLabel(code) {
  switch (code) {
    case 'consent_missing': return '필수 동의를 완료해주세요.';
    case 'photos_incomplete': return '얼굴 3장을 모두 업로드해주세요.';
    case 'body_profile_missing': return '신체 정보를 입력해주세요.';
    case 'purge_in_progress': return '삭제가 진행 중이라 잠시 후 다시 시도해주세요.';
    default: return code;
  }
}

// 생성 결과 게이트 바이트 → objectURL(언마운트 시 해제). <img src> 공개 URL 금지(§1.4).
function ResultImage({ uri }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    let alive = true;
    let u;
    fetchGenerationResultUrl(uri)
      .then((v) => { if (!alive) { URL.revokeObjectURL(v); return; } u = v; setUrl(v); })
      .catch(() => { /* 표시 실패 — 빈 타일 유지 */ });
    return () => { alive = false; if (u) URL.revokeObjectURL(u); };
  }, [uri]);
  return (
    <div className={s.resultTile}>
      {url ? <img src={url} alt="생성 결과" /> : <p className="hint" style={{ padding: 20 }}>불러오는 중…</p>}
    </div>
  );
}

export function ModelGenerate() {
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|blocked|ready|error
  const [blockers, setBlockers] = useState([]);
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [images, setImages] = useState([]);       // [{id,src}]
  const [selected, setSelected] = useState([]);    // asset id[]
  const [progress, setProgress] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [results, setResults] = useState(null);    // 게이트 URI[]

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const status = await getStatus();
      if (!status.canGenerate) { setBlockers(status.blockers || []); setPhase('blocked'); return; }
      const lib = await api.getLibrary();
      setProjects(Array.isArray(lib) ? lib : []);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const pickProject = async (pid) => {
    setProjectId(pid); setSelected([]); setImages([]); setResults(null);
    try {
      const product = await api.getProduct(pid);
      const imgs = (product.colors || []).flatMap((c) => c.images || []).filter((im) => im.id && im.src);
      setImages(imgs);
    } catch (e) {
      push?.(e.message || '상품 이미지를 불러오지 못했어요.', { icon: 'alertCircle' });
    }
  };

  const toggleImg = (id) => setSelected((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]));

  const onGenerate = async () => {
    if (!selected.length) { push?.('상품 이미지를 1장 이상 선택해주세요.', { icon: 'alertCircle' }); return; }
    setGenerating(true); setProgress(0); setResults(null);
    try {
      const { result } = await startGeneration(
        { productImageAssetIds: selected, projectId, options: {} },
        { onProgress: setProgress },
      );
      // §4 결과 shape 는 TBD — { results:[uri,...] } 가정, 배열/객체 형태 모두 방어적으로 흡수.
      const list = Array.isArray(result?.results) ? result.results : Array.isArray(result) ? result : [];
      setResults(list.map((r) => (typeof r === 'string' ? r : r?.uri)).filter(Boolean));
      push?.('생성이 완료됐어요.', { icon: 'check' });
    } catch (e) {
      push?.(e.message || '생성에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setGenerating(false);
    }
  };

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="정보를 불러오지 못했어요." onRetry={load} /></div></div>;
  if (phase === 'blocked') {
    return (
      <div className="wizard narrow">
        <div className="page-head"><h1>아직 생성할 수 없어요</h1><p>아래 항목을 먼저 완료해주세요.</p></div>
        <div className="surface">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {blockers.map((b) => <li key={b.code} className="hint" style={{ marginBottom: 6 }}>{blockerLabel(b.code)}</li>)}
          </ul>
          <Link to="/model" className={s.footerLink}><Icon name="chevRight" size={13} />온보딩으로 돌아가기</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard">
      <div className="page-head">
        <h1>내 모델로 착장 컷 만들기</h1>
        <p>상품 이미지를 선택하면 내 얼굴·신체로 착장 컷을 생성해요.</p>
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>1. 상품 선택</div>
        {projects.length === 0 ? (
          <p className="hint">보관함에 등록된 상품이 없어요. 먼저 상세페이지를 제작해주세요.</p>
        ) : (
          <div className={s.projectList}>
            {projects.map((p) => (
              <button key={p.id} type="button" className={`${s.projectRow}${projectId === p.id ? ' ' + s.on : ''}`} onClick={() => pickProject(p.id)}>
                {p.cover && <img src={p.cover} alt="" />}
                <span className={s.projectRowTitle}>{p.title}</span>
              </button>
            ))}
          </div>
        )}

        {images.length > 0 && (
          <>
            <div className={s.sectionLabel}>2. 상품 이미지 선택</div>
            <div className={s.assetGrid}>
              {images.map((im) => (
                <div key={im.id} className={`${s.assetTile}${selected.includes(im.id) ? ' ' + s.on : ''}`} onClick={() => toggleImg(im.id)}>
                  <img src={im.src} alt="" />
                  {selected.includes(im.id) && <span className={s.assetTileCheck}><Icon name="check" size={12} /></span>}
                </div>
              ))}
            </div>
          </>
        )}

        <Button variant="primary" block iconRight="arrowRight" style={{ marginTop: 20 }}
          onClick={onGenerate} disabled={generating || !selected.length}>
          {generating ? '생성 중…' : '내 모델로 생성하기'}
        </Button>
        {generating && <ProgressBar value={progress} label="생성하고 있어요" />}
      </div>

      {results && (
        <div className="surface">
          <div className={s.sectionLabel}>생성 결과</div>
          {results.length === 0
            ? <p className="hint">표시할 결과가 없어요.</p>
            : <div className={s.resultGrid}>{results.map((uri) => <ResultImage key={uri} uri={uri} />)}</div>}
        </div>
      )}
    </div>
  );
}

export default ModelGenerate;
