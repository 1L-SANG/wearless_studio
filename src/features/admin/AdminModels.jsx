/* 모델·유저 — 검색·필터 표 + 선택 행 상세.

   상세를 별도 라우트가 아니라 같은 화면 오른쪽에 붙인다. 운영자는 "이 모델 뭐지"를 확인하고
   목록으로 곧장 돌아온다 — 라우트를 갈면 그 왕복마다 목록이 다시 로드되고 스크롤을 잃는다. */
import { useCallback, useEffect, useState } from 'react';
import {
  adminListModels, adminModelDetail, adminSuspendModel, adminUnsuspendModel,
} from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import { useToast } from '@/components/ui.jsx';

const STATUS_FILTERS = [
  { value: '', label: '전체' },
  { value: 'pending', label: '대기' },
  { value: 'verified', label: '검증됨' },
  { value: 'suspended', label: '정지' },
];
const STATUS_LABEL = { pending: '대기', verified: '검증됨', suspended: '정지' };
const STATUS_VARIANT = { pending: 'secondary', verified: 'default', suspended: 'destructive' };
const won = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;
const day = (iso) => (iso ? iso.slice(0, 10) : '-');

function Detail({ modelId, onChanged }) {
  // useToast() 는 { push, dismiss } 를 준다(.show 는 없다) — AdminApplications.jsx 의
  // 기존 소비 방식과 맞춘다. 여기서 잘못 불러 조용히 no-op 되면, 정지 실패 같은 서버 거부
  // 메시지가 화면에 안 뜨는 채로 사용자만 남는다 — 가드레일 안내가 핵심인 화면이라 치명적.
  const { push } = useToast();
  const [data, setData] = useState(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setData(null);
    adminModelDetail(modelId).then(setData).catch((e) => push?.(e.message, { icon: 'alertCircle' }));
  }, [modelId, push]);

  useEffect(() => { load(); }, [load]);

  if (!data) return <Skeleton className="h-64" />;

  const { model, licenses, settlements, enrollment } = data;
  const suspended = model.status === 'suspended';

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
      setReason('');
      load();
      onChanged?.();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">{model.displayName}</CardTitle>
          <Badge variant={STATUS_VARIANT[model.status]}>{STATUS_LABEL[model.status]}</Badge>
        </div>
        <CardDescription>{model.email || '연결된 계정 없음 (플랫폼 온보딩)'}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">라이선스 {licenses.length}건</h4>
          {licenses.length === 0 && <p className="text-muted-foreground">없음</p>}
          {licenses.map((l) => (
            <div key={l.id} className="flex gap-3">
              <span>{l.status}</span><span>{won(l.unitPrice)}</span><span>~{day(l.validUntil)}</span>
            </div>
          ))}
        </section>
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">최근 정산</h4>
          {settlements.length === 0 && <p className="text-muted-foreground">없음</p>}
          {settlements.map((s) => (
            <div key={s.id} className="flex gap-3">
              <span>{day(s.createdAt)}</span><span>{won(s.totalAmount)}</span><span>{s.chainStatus}</span>
            </div>
          ))}
        </section>
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">생체등록</h4>
          <p>{enrollment ? `${enrollment.status} · ${day(enrollment.completedAt)}` : '기록 없음'}</p>
        </section>
        <section className="border-t border-border pt-4">
          {suspended ? (
            <Button variant="outline" disabled={busy} onClick={() => act(() => adminUnsuspendModel(model.id))}>
              정지 해제 (정지 직전 상태로 되돌아가요)
            </Button>
          ) : (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="정지 사유 (기록에 남아요)"
                className="sm:flex-1"
              />
              <Button
                variant="destructive"
                disabled={busy || !reason.trim()}
                onClick={() => act(() => adminSuspendModel(model.id, reason.trim()))}
              >
                정지
              </Button>
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

export function AdminModels() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [items, setItems] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    setItems(null);
    adminListModels({ q: q.trim(), status }).then((d) => setItems(d.items)).catch(() => setItems([]));
  }, [q, status]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="모델명 또는 계정 이메일"
          className="w-64"
        />
        {STATUS_FILTERS.map((f) => (
          <Button key={f.value} size="sm" variant={f.value === status ? 'default' : 'outline'} onClick={() => setStatus(f.value)}>
            {f.label}
          </Button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_24rem]">
        <Card>
          <CardContent className="p-0">
            {!items && <Skeleton className="h-64" />}
            {items && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>모델</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>계정</TableHead>
                    <TableHead>라이선스</TableHead>
                    <TableHead>최근 정산</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((m) => (
                    <TableRow
                      key={m.id}
                      onClick={() => setSelected(m.id)}
                      className={`cursor-pointer ${selected === m.id ? 'bg-muted' : ''}`}
                    >
                      <TableCell>{m.displayName}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[m.status]}>{STATUS_LABEL[m.status]}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{m.email || '-'}</TableCell>
                      <TableCell>{m.licenseCount}</TableCell>
                      <TableCell className="text-muted-foreground">{day(m.lastSettlementAt)}</TableCell>
                    </TableRow>
                  ))}
                  {items.length === 0 && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">결과 없음</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {selected && <Detail modelId={selected} onChanged={load} />}
      </div>
    </div>
  );
}
