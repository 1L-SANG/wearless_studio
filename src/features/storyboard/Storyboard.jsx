/* =============================================================
   storyboard/Storyboard.jsx — 상세페이지 초안 / 콘티보드 (PRD §8).
   카드 리스트(중앙) → 카드 선택 시 좌/우 분할 + 인스펙터. 컷 종류
   탭(적응형 방향·샷), 분위기 예시(생성예시/내 레퍼런스), 포즈, 추가
   옵션, 수정 잠금. 하단 고정 액션바(카피라이팅 토글 + 크레딧 예고).
   배경 선택은 PRD §8.5에 따라 노출하지 않음(bg 필드는 데이터에 보존).
   ============================================================= */
import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Placeholder } from '@/mock/placeholders.js';
import { useAppStore } from '@/store/useAppStore.js';
import { CREDIT_COSTS } from '@/lib/limits.js';
import { Button, IconButton } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import { Chips, Segmented, Tabs, Toggle } from '@/components/Form.jsx';
import { PageHead } from '@/features/shell/PageHead.jsx';
import { useToast } from '@/components/Toast.jsx';
import styles from './Storyboard.module.css';

const PRODUCT_DIRS = [{ value: 'front', label: '앞면' }, { value: 'back', label: '뒷면' }];
const PRODUCT_SHOTS = [{ value: 'ghost', label: '고스트컷' }, { value: 'hanger', label: '행거컷' }, { value: 'flatlay', label: '플랫레이샷' }];
const FACES = [{ value: 'same', label: '동일' }, { value: 'show', label: '노출' }, { value: 'hide', label: '비노출' }];
const ANGLES = [{ value: 'same', label: '동일' }, { value: 'low', label: '로우' }, { value: 'high', label: '하이' }];
const SOURCE_KIND = { studio: 'horizon', daily: 'styling', product: 'product', mine: 'mine' };

const newBlock = () => ({
  id: 'blk_' + Math.random().toString(36).slice(2, 8),
  kind: 'horizon', title: '호리존컷', direction: 'front', shot: 'full',
  colorId: 'col1', source: 'studio', thumb: Placeholder.photo('new' + Date.now(), 'horizon', 240, 320),
  poseThumb: Placeholder.pose('stand'), poseLabel: '서기', bgThumb: Placeholder.scene('studio'), bgLabel: '스튜디오',
});

export function Storyboard() {
  const navigate = useNavigate();
  const toast = useToast();
  const storeStoryboard = useAppStore((s) => s.storyboard);
  const setStoryboard = useAppStore((s) => s.setStoryboard);
  const copywriting = useAppStore((s) => s.copywriting);
  const setCopywriting = useAppStore((s) => s.setCopywriting);
  const catalogs = useAppStore((s) => s.catalogs);
  const analysis = useAppStore((s) => s.analysis);
  const product = useAppStore((s) => s.product);

  const [blocks, setBlocks] = useState(storeStoryboard.length ? storeStoryboard : []);
  const [selectedId, setSelectedId] = useState(null);
  const [opened, setOpened] = useState(false);
  const [refTab, setRefTab] = useState('gen');
  const [myRefs, setMyRefs] = useState([]);
  const [moreOpen, setMoreOpen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [lockWarn, setLockWarn] = useState(false);
  const snapshot = useRef(null);
  const dragIndex = useRef(null);

  useEffect(() => {
    if (blocks.length) return;
    api.getStoryboard().then(setBlocks);
  }, [blocks.length]);

  const selected = blocks.find((b) => b.id === selectedId) || null;
  const isMine = selected?.source === 'mine';
  const cutSources = catalogs?.cutSources || [];
  const directions = selected?.source === 'product' ? PRODUCT_DIRS : (catalogs?.directions || []);
  const shots = selected?.source === 'product' ? PRODUCT_SHOTS : (catalogs?.shotTypes || []);
  const poses = catalogs?.poses || [];
  const genExamples = catalogs?.genExamples || [];

  const aiCount = blocks.filter((b) => b.source !== 'mine').length;
  const mineCount = blocks.filter((b) => b.source === 'mine').length;
  const credit = aiCount * CREDIT_COSTS.storyboardPerCut;

  const swatchHexOf = useMemo(() => (colorId) => {
    const group = (product?.colors || []).find((c) => c.id === colorId);
    if (!group || group.isBase) return null;
    return (catalogs?.swatchColors || []).find((s) => s.label === group.name)?.hex || null;
  }, [product, catalogs]);

  /* ---- selection + edit lock (PRD §8.8) ---- */
  const selectCard = (id) => {
    if (dirty && id !== selectedId) { setLockWarn(true); return; }
    setSelectedId(id); setOpened(true); setDirty(false); setLockWarn(false);
    snapshot.current = blocks.find((b) => b.id === id) || null;
  };
  const update = (patch) => {
    setBlocks((bs) => bs.map((b) => (b.id === selectedId ? { ...b, ...patch } : b)));
    if (!isMine) setDirty(true);
  };
  const commit = () => { setDirty(false); setLockWarn(false); snapshot.current = blocks.find((b) => b.id === selectedId); };
  const restore = () => {
    if (snapshot.current) setBlocks((bs) => bs.map((b) => (b.id === selectedId ? snapshot.current : b)));
    setDirty(false); setLockWarn(false);
  };

  /* ---- list ops ---- */
  const move = (i, dir) => setBlocks((bs) => {
    const j = i + dir; if (j < 0 || j >= bs.length) return bs;
    const next = [...bs]; [next[i], next[j]] = [next[j], next[i]]; return next;
  });
  const duplicate = (id) => setBlocks((bs) => {
    const i = bs.findIndex((b) => b.id === id);
    const copy = { ...bs[i], id: 'blk_' + Math.random().toString(36).slice(2, 8) };
    const next = [...bs]; next.splice(i + 1, 0, copy); return next;
  });
  const remove = (id) => {
    setBlocks((bs) => bs.filter((b) => b.id !== id));
    if (selectedId === id) { setSelectedId(null); setDirty(false); }
  };
  const insertAt = (i) => setBlocks((bs) => { const next = [...bs]; next.splice(i, 0, newBlock()); return next; });
  const reorder = (from, to) => setBlocks((bs) => {
    if (from == null || from === to) return bs;
    const next = [...bs]; const [m] = next.splice(from, 1); next.splice(to, 0, m); return next;
  });

  const changeSource = (src) => update({ source: src, kind: SOURCE_KIND[src], title: cutSources.find((s) => s.value === src)?.label || '' });

  const addRef = () => setMyRefs((r) => [...r, { id: 'ref_' + Math.random().toString(36).slice(2, 7), src: Placeholder.any('ref' + Date.now()) }]);
  const removeRef = (id) => setMyRefs((r) => r.filter((x) => x.id !== id));

  const generate = () => { setStoryboard(blocks); api.saveStoryboard(blocks); navigate('/create/generating'); };

  return (
    <div className={`${styles.wrap} ${opened ? styles.split : ''}`}>
      {!opened && <PageHead title="상세페이지 초안 구성" sub="지금 보이는 이미지들은 예시예요. 느낌을 보고 필요한 컷을 수정하며 상세페이지를 생성해보세요." />}

      <div className={styles.body}>
        {/* ---- left: block card list ---- */}
        <div className={styles.list}>
          {blocks.map((b, i) => {
            const hex = swatchHexOf(b.colorId);
            return (
              <div key={b.id}>
                <div
                  className={`${styles.blockCard} ${selectedId === b.id ? styles.cardSel : ''}`}
                  draggable
                  onDragStart={() => { dragIndex.current = i; }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => { reorder(dragIndex.current, i); dragIndex.current = null; }}
                  onClick={() => selectCard(b.id)}
                >
                  <span className={styles.grip}><Icon name="gripV" size={16} /></span>
                  <div className={styles.cardThumb}><img src={b.thumb} alt={b.title} /></div>
                  <div className={styles.cardMain}>
                    <div className={styles.cardTitle}>{b.title}</div>
                    <div className={styles.cardMeta}>
                      {b.source !== 'mine' && <>
                        <span>{(directions.find((d) => d.value === b.direction)?.label) || b.direction}</span>
                        <span>·</span>
                        <span>{b.shot}</span>
                      </>}
                      {hex && <span className={styles.colorCircle} style={{ '--c': hex }} />}
                      {b.source === 'mine' && <span className={styles.mineTag}>내 이미지</span>}
                    </div>
                  </div>
                  <div className={styles.quick}>
                    <IconButton name="arrowUp" size="sm" title="위로" onClick={(e) => { e.stopPropagation(); move(i, -1); }} />
                    <IconButton name="arrowDown" size="sm" title="아래로" onClick={(e) => { e.stopPropagation(); move(i, 1); }} />
                    <IconButton name="copy" size="sm" title="복제" onClick={(e) => { e.stopPropagation(); duplicate(b.id); }} />
                    <IconButton name="trash" size="sm" title="삭제" onClick={(e) => { e.stopPropagation(); remove(b.id); }} />
                  </div>
                </div>
                <button type="button" className={styles.insert} onClick={() => insertAt(i + 1)}><Icon name="plus" size={13} />블록 추가</button>
              </div>
            );
          })}
        </div>

        {/* ---- right: inspector ---- */}
        {opened && (
          <aside className={styles.inspector}>
            {!selected && (
              <div className={styles.inspectorEmpty}>
                <Icon name="image" size={22} />
                <p>카드를 선택하면 세부 옵션이 열려요.</p>
                <Button variant="ghost" size="sm" icon="upload" onClick={() => insertAt(blocks.length)}>내 이미지 업로드</Button>
              </div>
            )}

            {selected && (
              <>
                {lockWarn && <div className={styles.lockWarn}><Icon name="alertCircle" size={15} />수정 완료를 먼저 눌러주세요.</div>}

                <Tabs options={cutSources} value={selected.source} onChange={changeSource} />

                {isMine ? (
                  <div className={styles.mineBody}>
                    <div className={styles.mineImg}><img src={selected.thumb} alt="" /></div>
                    <p className={styles.hint}>내 이미지 블록은 업로드한 이미지를 그대로 사용해요.</p>
                  </div>
                ) : (
                  <>
                    <div className={styles.field}><span className={styles.fieldLabel}>방향</span>
                      <Chips options={directions} value={selected.direction} onChange={(v) => update({ direction: v })} />
                    </div>
                    <div className={styles.field}><span className={styles.fieldLabel}>샷 종류</span>
                      <Chips options={shots} value={selected.shot} onChange={(v) => update({ shot: v })} />
                    </div>

                    <div className={styles.field}>
                      <div className={styles.fieldRowBetween}><span className={styles.fieldLabel}>분위기 예시</span>
                        <Segmented options={[{ value: 'gen', label: '생성예시' }, { value: 'mine', label: '내 레퍼런스' }]} value={refTab} onChange={setRefTab} />
                      </div>
                      {refTab === 'gen' ? (
                        <div className={styles.exGrid}>
                          {genExamples.slice(0, 6).map((ex) => <div key={ex.id} className={styles.exThumb}><img src={ex.thumb} alt="" /></div>)}
                        </div>
                      ) : (
                        <div className={styles.exGrid}>
                          {myRefs.map((r) => (
                            <div key={r.id} className={styles.exThumb}>
                              <img src={r.src} alt="" />
                              <button type="button" className={styles.refRemove} onClick={() => removeRef(r.id)}><Icon name="x" size={12} /></button>
                            </div>
                          ))}
                          <button type="button" className={styles.refAdd} onClick={addRef}><Icon name="plus" size={18} /></button>
                        </div>
                      )}
                    </div>

                    <div className={styles.field}><span className={styles.fieldLabel}>포즈</span>
                      <div className={styles.poseGrid}>
                        {poses.map((p) => (
                          <button key={p.id} type="button" className={`${styles.poseCell} ${selected.poseLabel === p.label ? styles.poseOn : ''} ${p.auto ? styles.poseAuto : ''}`} onClick={() => update({ poseLabel: p.label, poseThumb: p.thumb || selected.poseThumb })}>
                            {p.auto ? <Icon name="sparkles" size={15} /> : <img src={p.thumb} alt={p.label} />}
                            <span>{p.label}</span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <button type="button" className={styles.moreToggle} onClick={() => setMoreOpen((v) => !v)}>
                      추가 옵션<Icon name={moreOpen ? 'chevUp' : 'chevDown'} size={16} />
                    </button>
                    {moreOpen && (
                      <div className={styles.moreBody}>
                        <div className={styles.field}><span className={styles.fieldLabel}>모델 얼굴</span>
                          <Chips options={FACES} value={selected.face || 'same'} onChange={(v) => update({ face: v })} />
                        </div>
                        <div className={styles.field}><span className={styles.fieldLabel}>앵글</span>
                          <Chips options={ANGLES} value={selected.angle || 'same'} onChange={(v) => update({ angle: v })} />
                        </div>
                      </div>
                    )}

                    <div className={styles.lockActions}>
                      <Button variant="ghost" block disabled={!dirty} onClick={restore}>원래대로</Button>
                      <Button variant="primary" block disabled={!dirty} onClick={commit}>수정 완료</Button>
                    </div>
                  </>
                )}
              </>
            )}
          </aside>
        )}
      </div>

      {/* ---- bottom action bar (PRD §8.9) ---- */}
      <div className={styles.actionbar}>
        <Button variant="ghost" icon="arrowLeft" onClick={() => navigate('/create/mannequin')}>이전</Button>
        <div className={styles.summary}>AI 생성 {aiCount}컷 · 셀러 사진 {mineCount}컷</div>
        <label className={styles.copyToggle}>
          <span className={styles.copyText}><b>카피라이팅 {copywriting ? 'ON' : 'OFF'}</b><i>AI가 카피를 자동으로 넣어요</i></span>
          <Toggle on={copywriting} onChange={setCopywriting} />
        </label>
        <Button variant="primary" size="lg" icon="sparkles" onClick={generate}>이대로 생성하기 · {credit} 크레딧</Button>
      </div>
    </div>
  );
}

export default Storyboard;
