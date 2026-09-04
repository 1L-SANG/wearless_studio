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

// fm_models_status_check(백엔드 MODEL_STATUSES)가 허용하는 네 값 전부를 다뤄야 한다.
// reverification_required 라벨은 ModelHub.jsx 의 MODEL_STATUS_LABEL 과 맞춘다 — 운영자
// 화면과 모델 본인 화면이 같은 상태를 다른 말로 부르면 안 된다.
const STATUS_FILTERS = [
  { value: '', label: '전체' },
  { value: 'pending', label: '대기' },
  { value: 'verified', label: '검증됨' },
  { value: 'reverification_required', label: '재검증 필요' },
  { value: 'suspended', label: '정지' },
];
const STATUS_LABEL = {
  pending: '대기', verified: '검증됨', reverification_required: '재검증 필요', suspended: '정지',
};
const STATUS_VARIANT = {
  pending: 'secondary', verified: 'default', reverification_required: 'secondary', suspended: 'destructive',
};
// check 제약에 다섯 번째 값이 늘어나도, 빈 배지(undefined → 스타일 없이 텅 빈 pill)
// 대신 원문자열을 보여준다 — 안 보이는 것보다 못생긴 게 낫다.
const statusLabel = (status) => STATUS_LABEL[status] || status;
const won = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;
const day = (iso) => (iso ? iso.slice(0, 10) : '-');

function Detail({ modelId, onChanged }) {
  // useToast() 는 { push, dismiss } 를 준다(.show 는 없다) — AdminApplications.jsx 의
  // 기존 소비 방식과 맞춘다. 여기서 잘못 불러 조용히 no-op 되면, 정지 실패 같은 서버 거부
  // 메시지가 화면에 안 뜨는 채로 사용자만 남는다 — 가드레일 안내가 핵심인 화면이라 치명적.
  const { push } = useToast();
  const [data, setData] = useState(null);
  // 목록의 fetch 실패와 같은 문제 — 예전엔 실패해도 data 가 계속 null 이라 패널 전체가
  // <Skeleton> 하나로 영원히 멈췄다(카드 틀조차 없었다). 상세는 실패가 낯설지 않다(모델을
  // 고른 직후 잠깐의 5xx 등) — 패널 틀은 항상 그리고, 실패는 안에서 보여주고 다시 시도를
  // 준다.
  const [detailError, setDetailError] = useState(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setData(null);
    setDetailError(null);
    adminModelDetail(modelId)
      .then(setData)
      .catch((e) => setDetailError(e.message || '모델 정보를 불러오지 못했어요.'));
  }, [modelId]);

  useEffect(() => { load(); }, [load]);

  if (detailError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">모델 정보를 불러오지 못했어요</CardTitle>
          <CardDescription>{detailError}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={load}>다시 시도</Button>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="pt-5">
          <Skeleton className="h-64" />
        </CardContent>
      </Card>
    );
  }

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
          <Badge variant={STATUS_VARIANT[model.status]}>{statusLabel(model.status)}</Badge>
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
  // 실패도 빈 배열로 떨어뜨리면 "모델이 없어요" 와 "요청이 실패했어요" 가 화면에서
  // 똑같이 "결과 없음" 으로 보인다 — 세션 만료(403)·5xx·네트워크 단절을 운영자가 구분할
  // 방법이 없어진다. 이 콘솔의 존재 이유가 "지금 시스템에 뭐가 진짜인지 알려주는 것"이라
  // 실패를 빈 목록으로 위장하면 안 된다.
  const [listError, setListError] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    setItems(null);
    setListError(null);
    adminListModels({ q: q.trim(), status })
      .then((d) => setItems(d.items))
      .catch((e) => setListError(e.message || '목록을 불러오지 못했어요.'));
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
            {listError && (
              <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted-foreground">
                <p>{listError}</p>
                <Button variant="outline" size="sm" onClick={load}>다시 시도</Button>
              </div>
            )}
            {!items && !listError && <Skeleton className="h-64" />}
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
                      tabIndex={0}
                      role="button"
                      aria-pressed={selected === m.id}
                      onClick={() => setSelected(m.id)}
                      // TableRow 는 props 를 그대로 <tr> 로 흘려보낸다. 클릭만 걸려 있으면
                      // 키보드만 쓰는 관리자는 이 행을 절대 못 연다 — staff 화면의 검색
                      // input onKeyDown 과 같은 모양으로, Enter·Space 둘 다 받는다.
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setSelected(m.id);
                        }
                      }}
                      className={`cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selected === m.id ? 'bg-muted' : ''}`}
                    >
                      <TableCell>{m.displayName}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[m.status]}>{statusLabel(m.status)}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {/* auth.users.email 은 카카오 로그인 이메일 동의가 선택이라 없을 수
                            있다 — 그렇다고 '-' 로만 보여주면, 운영자가 실제로 아는 지원서
                            이메일(contact_email)로도 이 모델을 못 알아본다. 값을 보여줄 땐
                            어느 쪽 출처인지 밝힌다 — auth 이메일과 헷갈리면 안 된다. */}
                        {m.email
                          || (m.applicationContactEmail
                            ? `${m.applicationContactEmail} (지원서 이메일)`
                            : '-')}
                      </TableCell>
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
