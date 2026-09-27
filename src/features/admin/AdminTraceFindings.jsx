/* 출처 추적 · 자동 발견 — 순찰(네이버·지그재그)과 모델 제보가 쌓은 발견을 판정한다(2026-09-27).

   판정은 네 가지다. '셀러 정상 사용 · 판매처 기억'을 누르면 그 셀러의 그 판매처는 다음 순찰부터
   자동으로 정상 분류되고 슬랙이 울리지 않는다 — 셀러에게 판매처 등록을 요구하지 않고 구분하는 방법.
   셀러는 서버가 마스킹해서 준다. 이미지는 어디에도 저장되지 않는다(해시만). */
import { useCallback, useEffect, useRef, useState } from 'react';
import { adminListTraceFindings, adminUpdateTraceFinding } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/admin-ui/table.jsx';
import { seoulClock, seoulDateKey } from '@/lib/datetime.js';
import {
  FINDING_STATUSES, findingMatchText, findingSourceLabel, findingStatusLabel, purgeNote, targetText,
} from './adminTraceFindings.js';
import { licenseStatusLabel } from './adminTrace.js';

const NOTICE = {
  seller_own: '셀러 정상 사용으로 표시했어요.',
  misuse: '무단 사용으로 표시했어요.',
  dismissed: '관련 없음으로 표시했어요.',
  new: '확인 전으로 되돌렸어요.',
};

export function AdminTraceFindings() {
  const [status, setStatus] = useState('new');
  const [items, setItems] = useState(null);
  const [nextCursor, setNextCursor] = useState(null);
  const [listError, setListError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(false);
  const listing = useRef(false);
  const saving = useRef(false);
  const version = useRef(0);
  const activeStatus = useRef('new');

  const load = useCallback(async (cursor = null) => {
    if (listing.current || saving.current) return;
    listing.current = true;
    const mine = ++version.current;
    setLoading(true);
    setListError(null);
    try {
      const payload = await adminListTraceFindings({
        status: activeStatus.current || undefined, source: undefined, cursor: cursor || undefined,
      });
      if (!alive.current || mine !== version.current) return;
      setItems(previous => (cursor ? [...(previous || []), ...payload.items] : payload.items));
      setNextCursor(payload.nextCursor || null);
    } catch (error) {
      if (alive.current && mine === version.current) {
        setListError({ message: error.message || '발견 목록을 불러오지 못했어요.', cursor });
      }
    } finally {
      if (alive.current && mine === version.current) {
        listing.current = false;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
      version.current += 1;
      listing.current = false;
    };
  }, [load]);

  const changeFilter = next => {
    if (saving.current || activeStatus.current === next) return;
    activeStatus.current = next;
    setStatus(next);
    setItems(null);
    setNextCursor(null);
    setSaveError('');
    setNotice('');
    listing.current = false;
    return load();
  };

  const decide = async (finding, nextStatus, rememberStore = false) => {
    if (saving.current || listing.current) return;
    saving.current = true;
    setBusyId(finding.id);
    setSaveError('');
    setNotice('');
    try {
      const updated = await adminUpdateTraceFinding(finding.id, { status: nextStatus, rememberStore });
      if (!alive.current) return;
      setItems(previous => (previous || [])
        .map(item => (item.id === updated.id ? { ...item, status: updated.status } : item))
        .filter(item => !activeStatus.current || item.status === activeStatus.current));
      setNotice(updated.storeRemembered
        ? '셀러 정상 사용으로 표시하고 판매처를 기억했어요. 다음 순찰부터 이 판매처는 알리지 않아요.'
        : NOTICE[updated.status] || '판정을 바꿨어요.');
    } catch (error) {
      if (alive.current) setSaveError(error.message || '판정을 바꾸지 못했어요.');
    } finally {
      saving.current = false;
      if (alive.current) setBusyId(null);
    }
  };

  const locked = busyId !== null || loading;
  return <div className="flex min-w-0 flex-col gap-4">
    <p className="text-sm text-muted-foreground">하루 한 번 네이버·지그재그에서 상품명으로 찾은 대표 이미지와, 모델이 직접 보낸 제보를 배포본과 대조한 결과예요. 셀러 본인 스토어의 정상 사용이면 판매처를 기억해 두세요 — 다음부터 알리지 않아요.</p>
    <div className="flex flex-wrap gap-2" aria-label="발견 판정 상태">
      {FINDING_STATUSES.map(filter => <Button key={filter.value || 'all'} size="sm"
        variant={status === filter.value ? 'default' : 'outline'} aria-pressed={status === filter.value}
        disabled={busyId !== null} onClick={() => changeFilter(filter.value)}>{filter.label}</Button>)}
    </div>
    {listError && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm">
      <p>{listError.message}</p>
      <Button variant="outline" size="sm" disabled={loading} onClick={() => load(listError.cursor)}>다시 시도</Button>
    </div>}
    {saveError && <p role="alert" className="text-sm text-destructive">{saveError}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <Card className="min-w-0"><CardContent className="p-0">
      {!items && !listError && <Skeleton className="h-64" aria-label="발견 목록을 불러오고 있어요" />}
      {items && <Table>
        <TableHeader><TableRow>
          <TableHead>마지막 발견</TableHead><TableHead>출처</TableHead><TableHead>어디서</TableHead><TableHead>근거</TableHead>
          <TableHead>셀러 · 모델</TableHead><TableHead>상태</TableHead><TableHead>판정</TableHead>
        </TableRow></TableHeader>
        <TableBody>{items.map(item => {
          const where = item.source === 'model_report' ? item.reportPageUrl : item.productUrl;
          const note = purgeNote(item);
          return <TableRow key={item.id}>
            <TableCell className="whitespace-nowrap text-muted-foreground">{seoulDateKey(item.lastSeenAt)}<br />{seoulClock(item.lastSeenAt)}{item.seenCount > 1 && <><br />{item.seenCount}회 발견</>}</TableCell>
            <TableCell><div className="flex flex-col gap-1"><span>{findingSourceLabel(item)}</span>
              {item.reporterModelName && <span className="text-xs text-muted-foreground">제보: {item.reporterModelName}</span>}</div></TableCell>
            <TableCell className="max-w-xs"><div className="flex flex-col gap-1 break-all">
              {where ? <a className="text-sm underline" href={where} target="_blank" rel="noreferrer noopener">{item.productTitle || where}</a>
                : <span className="text-sm text-muted-foreground">주소 없음</span>}
              {item.storeName && <span className="text-xs text-muted-foreground">판매처 {item.storeName}</span>}
              {item.reportNote && <span className="text-xs whitespace-pre-wrap">{item.reportNote}</span>}
              {note && <span className="text-xs text-muted-foreground">{note}</span>}
            </div></TableCell>
            <TableCell className="text-sm"><div className="flex flex-col gap-1">
              <span>{findingMatchText(item)}</span>
              <span className="text-xs text-muted-foreground">{targetText(item)}{item.license?.status ? ` · 라이선스 ${licenseStatusLabel(item.license.status)}` : ''}{item.publicationRevokedAt ? ' · 배포본 철회됨' : ''}</span>
            </div></TableCell>
            <TableCell><div className="flex flex-col">
              <span>{item.seller?.nameMasked || item.seller?.emailMasked || '-'}</span>
              {item.seller?.nameMasked && item.seller?.emailMasked && <span className="text-xs text-muted-foreground">{item.seller.emailMasked}</span>}
              <span className="text-xs text-muted-foreground">모델 {item.model?.displayName || '-'}</span>
            </div></TableCell>
            <TableCell><Badge variant={item.status === 'misuse' ? 'default' : 'outline'}>{findingStatusLabel(item.status)}</Badge></TableCell>
            <TableCell><div className="flex min-w-36 flex-col items-start gap-1">
              {item.status !== 'misuse' && <Button size="sm" variant="outline" disabled={locked} onClick={() => decide(item, 'misuse')}>무단 사용</Button>}
              {item.status !== 'seller_own' && item.canRememberStore && <Button size="sm" variant="outline" disabled={locked} onClick={() => decide(item, 'seller_own', true)}>셀러 정상 사용 · 판매처 기억</Button>}
              {item.status !== 'seller_own' && !item.canRememberStore && item.target && <Button size="sm" variant="outline" disabled={locked} onClick={() => decide(item, 'seller_own')}>셀러 정상 사용</Button>}
              {item.status !== 'dismissed' && <Button size="sm" variant="ghost" disabled={locked} onClick={() => decide(item, 'dismissed')}>관련 없음</Button>}
              {item.status !== 'new' && <Button size="sm" variant="ghost" disabled={locked} onClick={() => decide(item, 'new')}>확인 전으로</Button>}
            </div></TableCell>
          </TableRow>;
        })}{items.length === 0 && <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
          {nextCursor ? '다음 발견을 보려면 더 보기를 눌러 주세요.' : '이 상태의 발견이 없어요.'}</TableCell></TableRow>}</TableBody>
      </Table>}
    </CardContent></Card>
    {nextCursor && !listError && <Button variant="outline" disabled={locked} onClick={() => load(nextCursor)}>{loading ? '불러오는 중…' : '더 보기'}</Button>}
  </div>;
}
