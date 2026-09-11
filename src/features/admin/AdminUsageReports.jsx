import { useCallback, useEffect, useRef, useState } from 'react';
import { adminListUsageReports, adminUpdateUsageReportStatus } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import { seoulClock, seoulDateKey } from '@/lib/datetime.js';
import { replaceUsageReportStatus } from './adminUsageReports.js';

const STATUS_FILTERS = [
  { value: 'open', label: '처리 전' },
  { value: 'closed', label: '처리 완료' },
  { value: '', label: '전체' },
];

export function AdminUsageReports() {
  const [status, setStatus] = useState('open');
  const [items, setItems] = useState(null);
  const [nextCursor, setNextCursor] = useState(null);
  const [listError, setListError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [notice, setNotice] = useState('');
  const alive = useRef(false);
  const listing = useRef(false);
  const saving = useRef(false);
  const listVersion = useRef(0);
  const activeStatus = useRef('open');

  const load = useCallback(async (cursor = null) => {
    if (listing.current || saving.current) return;
    listing.current = true;
    const version = ++listVersion.current;
    setLoading(true);
    setListError(null);
    try {
      const payload = await adminListUsageReports({
        status: activeStatus.current || undefined, cursor: cursor || undefined,
      });
      if (!alive.current || version !== listVersion.current) return;
      setItems((previous) => cursor ? [...(previous || []), ...payload.items] : payload.items);
      setNextCursor(payload.nextCursor || null);
    } catch (error) {
      if (alive.current && version === listVersion.current) {
        setListError({ message: error.message || '신고 목록을 불러오지 못했어요.', cursor });
      }
    } finally {
      if (alive.current && version === listVersion.current) {
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
      listVersion.current += 1;
      listing.current = false;
    };
  }, [load]);

  const changeFilter = (nextStatus) => {
    if (saving.current || activeStatus.current === nextStatus) return;
    activeStatus.current = nextStatus;
    setStatus(nextStatus);
    setItems(null);
    setNextCursor(null);
    setSaveError(null);
    setNotice('');
    listing.current = false;
    return load();
  };

  const setReportStatus = async (reportId, status) => {
    if (saving.current || listing.current) return;
    saving.current = true;
    setBusyId(reportId);
    setSaveError(null);
    setNotice('');
    try {
      const updated = await adminUpdateUsageReportStatus(reportId, status);
      if (!alive.current) return;
      setItems((previous) => replaceUsageReportStatus(previous || [], updated)
        .filter((item) => !activeStatus.current || item.status === activeStatus.current));
      setNotice(status === 'closed' ? '신고를 종결했어요.' : '신고를 다시 열었어요.');
    } catch (error) {
      if (alive.current) {
        setSaveError({ reportId, status, message: error.message || '신고 상태를 바꾸지 못했어요.' });
      }
    } finally {
      saving.current = false;
      if (alive.current) setBusyId(null);
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">사용 신고</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          모델이 접수한 신고를 최신순으로 확인해요. 종결 처리는 신고 상태만 바꾸며 라이선스를 취소하지 않아요.
        </p>
      </div>
      <div className="flex flex-wrap gap-2" aria-label="신고 처리 상태">
        {STATUS_FILTERS.map((filter) => (
          <Button key={filter.value || 'all'} size="sm"
            variant={status === filter.value ? 'default' : 'outline'}
            aria-pressed={status === filter.value} disabled={busyId !== null}
            onClick={() => changeFilter(filter.value)}>
            {filter.label}
          </Button>
        ))}
      </div>
      {listError && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-sm">
          <p>{listError.message}</p>
          <Button variant="outline" size="sm" disabled={loading} onClick={() => load(listError.cursor)}>
            다시 시도
          </Button>
        </div>
      )}
      {saveError && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-sm">
          <p>{saveError.message}</p>
          <Button variant="outline" size="sm" disabled={busyId !== null || loading}
            onClick={() => setReportStatus(saveError.reportId, saveError.status)}>
            변경 다시 시도
          </Button>
        </div>
      )}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      <Card className="min-w-0">
        <CardContent className="p-0">
          {!items && !listError && <Skeleton className="h-64" aria-label="신고 목록을 불러오고 있어요" />}
          {items && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>접수 시각</TableHead>
                  <TableHead>모델</TableHead>
                  <TableHead>사용 기록</TableHead>
                  <TableHead>신고 사유</TableHead>
                  <TableHead>상태</TableHead>
                  <TableHead>처리</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      {seoulDateKey(item.createdAt)}<br />{seoulClock(item.createdAt)}
                    </TableCell>
                    <TableCell className="break-all">{item.modelName || item.modelId}</TableCell>
                    <TableCell className="break-all">{item.paymentId || item.settlementId}</TableCell>
                    <TableCell className="max-w-xs whitespace-pre-wrap break-words">
                      {item.reason || '사유를 적지 않았어요.'}
                    </TableCell>
                    <TableCell>
                      <Badge variant={item.status === 'closed' ? 'secondary' : 'outline'}>
                        {item.status === 'closed' ? '종결' : '접수'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Button size="sm" variant="outline" disabled={busyId !== null || loading}
                        onClick={() => setReportStatus(item.id, item.status === 'open' ? 'closed' : 'open')}>
                        {busyId === item.id ? '처리 중...' : (item.status === 'open' ? '처리 완료' : '다시 열기')}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                      {nextCursor ? '다음 신고를 보려면 더 보기를 눌러 주세요.' : '이 상태의 신고가 없어요.'}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {nextCursor && !listError && (
        <Button variant="outline" disabled={loading || busyId !== null} onClick={() => load(nextCursor)}>
          {loading ? '불러오는 중…' : '더 보기'}
        </Button>
      )}
    </div>
  );
}
