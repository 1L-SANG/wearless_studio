/* 내 얼굴 찾기 신고(2026-09-27) — 모델이 발견한 이미지를 보내면 서버가 배포본과 대조해 관리자에게 알린다.

   모델에게는 접수와 관리자 판정 상태만 보인다. 셀러·배포본 정보는 관리자 콘솔에서만 본다.
   이미지는 저장하지 않는다(대조만 하고 버린다) — 그래서 발견한 페이지 주소를 함께 받는다. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { listMySightings, reportSighting } from '@/lib/api/facemarket.js';
import { seoulDate } from '@/lib/datetime.js';
import { sightingStatusLabel, validateSighting } from './sightingReport.js';
import s from './MyPage.module.css';

const emptyForm = () => ({ file: null, pageUrl: '', note: '' });

export function MyPageSightings() {
  const [items, setItems] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const alive = useRef(false);

  const load = useCallback(async () => {
    try {
      const payload = await listMySightings();
      if (alive.current) setItems(payload?.items || []);
    } catch {
      if (alive.current) setItems(previous => previous || []);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    return () => { alive.current = false; };
  }, [load]);

  const set = (key, value) => { setForm(previous => ({ ...previous, [key]: value })); setError(''); };

  const submit = async event => {
    event.preventDefault();
    if (busy.current) return;
    const invalid = validateSighting(form);
    if (invalid) { setError(invalid); return; }
    busy.current = true;
    setSending(true);
    setError('');
    try {
      await reportSighting(form.file, { pageUrl: form.pageUrl.trim(), note: form.note.trim() });
      if (!alive.current) return;
      setOpen(false);
      setForm(emptyForm());
      setNotice('신고를 접수했어요. 담당자가 확인하고 결과를 여기에 적어 둘게요.');
      await load();
    } catch (e) {
      if (alive.current) setError(e.message || '신고를 보내지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally {
      busy.current = false;
      if (alive.current) setSending(false);
    }
  };

  return <section className={s.sightings} aria-labelledby="sightings-title">
    <div className={s.panelHeading}><div><h2 id="sightings-title">여기 없는 곳에서 내 얼굴을 봤나요?</h2>
      <p className={s.panelCaption}>쇼핑몰 등에서 발견한 이미지를 보내 주세요. FaceMarket 배포본과 대조해 담당자가 확인해요. 올린 이미지는 대조에만 쓰고 저장하지 않아요.</p></div>
      {!open && <button type="button" className={s.quietButton} onClick={() => { setOpen(true); setNotice(''); }}>내 얼굴 찾기 신고</button>}
    </div>
    {notice && <p role="status">{notice}</p>}
    {open && <form className={s.sightingForm} onSubmit={submit}>
      <label>발견한 이미지<input type="file" accept="image/png,image/jpeg,image/webp" disabled={sending}
        onChange={event => set('file', event.target.files?.[0] || null)} /></label>
      <label>발견한 페이지 주소(있으면)<input type="url" inputMode="url" placeholder="https://" maxLength={500} value={form.pageUrl} disabled={sending}
        onChange={event => set('pageUrl', event.target.value)} /></label>
      <label>메모(선택)<textarea maxLength={1000} value={form.note} disabled={sending} onChange={event => set('note', event.target.value)} /></label>
      {error && <p className={s.error} role="alert">{error}</p>}
      <div className={s.sightingActions}>
        <button className={s.primaryButton} type="submit" disabled={sending || !form.file}>{sending ? '보내고 있어요' : '신고 보내기'}</button>
        <button className={s.quietButton} type="button" disabled={sending} onClick={() => { setOpen(false); setForm(emptyForm()); setError(''); }}>취소</button>
      </div>
    </form>}
    {items?.length > 0 && <ul className={s.sightingList}>{items.map(item => <li key={item.id}>
      <time dateTime={item.createdAt}>{seoulDate(item.createdAt)}</time>
      <span>{sightingStatusLabel(item.status)}</span>
      {item.pageUrl && <span className={s.sightingUrl}>{item.pageUrl}</span>}
    </li>)}</ul>}
  </section>;
}
