/* 사용자 — 셀러(Wearless)와 FaceMarket 가입자를 한 목록에서 **구분해서** 본다.

   두 서비스가 Supabase 프로젝트를 공유해서 profiles 한 테이블에 섞여 있다. 출처는
   profiles.app_origin 한 칸이고, 로그인할 때 서버가 Origin 헤더로 찍는다
   (server/app/app_origin.py). 그래서 '미상'이 존재한다 — 이번 스키마 변경 전에 가입해
   아직 다시 로그인하지 않은 계정이다. '미상'을 셀러로 뭉뚱그리지 않는 이유는 하나다:
   틀린 라벨은 빈 칸보다 나쁘다.

   검색은 타이핑마다가 아니라 Enter·버튼으로 보낸다(AdminModels 와 다른 점). 서버가 이
   목록 열람을 감사 원장에 남기기 때문에, 키 입력마다 요청하면 원장이 한 글자짜리 조회로
   가득 차서 "누가 무엇을 훑었나" 를 못 읽게 된다. */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { adminGrantCredits, adminListUsers } from '@/lib/api/facemarket.js';
import { httpAdapter } from '@/lib/api/httpAdapter.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { seoulDateKey } from '@/lib/datetime.js';
import { Textarea } from '@/components/admin-ui/textarea.jsx';
import { useToast } from '@/components/ui.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';

// 값은 서버의 USER_ORIGINS 와 같은 집합이어야 한다(facemarket_admin.validate_user_origin).
const ORIGIN_FILTERS = [
  { value: '', label: '전체', countKey: null },
  { value: 'facemarket', label: 'FaceMarket', countKey: 'facemarket' },
  { value: 'seller', label: 'Wearless', countKey: 'seller' },
  { value: 'both', label: '양쪽', countKey: 'both' },
  { value: 'unknown', label: '미상', countKey: 'unknown' },
];

const ORIGIN_LABEL = {
  facemarket: 'FaceMarket', seller: 'Wearless', both: '양쪽',
};
const ORIGIN_VARIANT = {
  facemarket: 'default', seller: 'secondary', both: 'outline',
};

const day = (iso) => seoulDateKey(iso);

/* null(미상)과 앞으로 늘어날 수 있는 값을 둘 다 다룬다 — 스키마에 여섯 번째 값이 생겨도
   빈 pill 대신 원문자열을 보여준다. */
function OriginBadge({ origin }) {
  if (!origin) {
    return <Badge variant="outline" className="text-muted-foreground">미상</Badge>;
  }
  return <Badge variant={ORIGIN_VARIANT[origin] || 'outline'}>{ORIGIN_LABEL[origin] || origin}</Badge>;
}

function CreditGrantForm({ user, onClose }) {
  const { push } = useToast();
  const [plans, setPlans] = useState(null);
  const [planError, setPlanError] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const formRef = useRef(null);
  const attempt = useRef(null);
  const inFlight = useRef(false);

  useEffect(() => { formRef.current?.focus(); }, []);

  useEffect(() => {
    let active = true;
    setPlanError(null);
    httpAdapter.getPricingPlans()
      .then((data) => { if (active) setPlans(data.filter((p) => p.kind === 'topup')); })
      .catch((e) => { if (active) setPlanError(e.message); });
    return () => { active = false; };
  }, [reload]);

  const submit = async (event) => {
    event.preventDefault();
    if (inFlight.current) return;
    const fields = new FormData(event.currentTarget);
    const body = {
      plan_code: fields.get('plan_code'),
      payer_name: fields.get('payer_name').trim(),
      amount_krw: Number(fields.get('amount_krw')),
      paid_at: fields.get('paid_at'),
      note: fields.get('note').trim() || null,
    };
    if (!body.payer_name) {
      setError('입금자명을 입력해 주세요.');
      return;
    }
    const signature = JSON.stringify(body);
    // 응답을 못 받은 같은 요청은 같은 키로 재시도한다.
    if (attempt.current?.signature !== signature) {
      attempt.current = { signature, key: crypto.randomUUID() };
    }
    inFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      const result = await adminGrantCredits(user.userId, body, attempt.current.key);
      push(`지급 완료, 잔액 ${result.available.toLocaleString('ko-KR')}`);
      onClose();
    } catch (e) {
      const message = e.status ? e.message
        : '지급 결과를 확인하지 못했어요. 이 폼을 유지한 채 입력을 바꾸지 않고 지급 확인을 다시 눌러 주세요.';
      setError(message);
      push(message, { icon: 'alertCircle' });
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  return (
    <form ref={formRef} tabIndex={-1} onSubmit={submit} aria-labelledby="credit-grant-title" className="flex flex-col gap-4 whitespace-normal p-3">
      <div>
        <h2 id="credit-grant-title" className="text-base font-semibold">크레딧 지급</h2>
        <p className="mt-1 break-all text-sm text-muted-foreground">
          {user.displayName || '이름 없음'}, {user.email || '이메일 없음'} ({user.userId})
        </p>
      </div>
      {planError && (
        <div className="flex items-center gap-3">
          <p role="alert" className="text-sm text-destructive">{planError}</p>
          <Button type="button" variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>다시 시도</Button>
        </div>
      )}
      {!plans && !planError && <p role="status" className="text-sm text-muted-foreground">요금제를 불러오는 중…</p>}
      {plans?.length === 0 && <p role="status" className="text-sm text-muted-foreground">지급할 수 있는 활성 요금제가 없어요.</p>}
      <fieldset disabled={saving || !plans?.length} className="grid min-w-0 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm sm:col-span-2">
          요금제
          <select name="plan_code" required defaultValue="" className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
            <option value="" disabled>요금제를 선택해 주세요</option>
            {plans?.map((p) => (
              <option key={p.code} value={p.code}>{p.name}, {p.credits.toLocaleString('ko-KR')} 크레딧, {p.price.toLocaleString('ko-KR')}원</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          입금자명
          <Input name="payer_name" required autoComplete="off" />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          입금액 (원)
          <Input name="amount_krw" type="number" min="0" step="1" required />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          입금일
          <Input name="paid_at" type="date" required />
        </label>
        <label className="flex flex-col gap-1.5 text-sm sm:col-span-2">
          메모 (선택)
          <Textarea name="note" rows={2} />
        </label>
      </fieldset>
      <p className="text-sm text-muted-foreground">입금액과 관계없이 선택한 요금제의 크레딧을 지급해요. 실제 입금액은 기록에 남아요.</p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={saving || !plans?.length}>{saving ? '지급 중…' : '지급 확인'}</Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onClose}>취소</Button>
      </div>
    </form>
  );
}

export function AdminUsers() {
  // input 의 값(term)과 **실제로 서버에 보낸 검색어**(query)를 나눈다. 안 나누면 타이핑
  // 중간 상태가 그대로 요청이 되고, 그게 곧 감사 원장 오염이다.
  const [term, setTerm] = useState('');
  const [query, setQuery] = useState('');
  const [origin, setOrigin] = useState('');

  // null = 아직 한 번도 응답을 못 받음, [] = 응답은 왔는데 0건. 둘을 합치면 로딩 중에도
  // "결과 없음" 이라는 확정적인 거짓말을 하게 된다(AdminStaff 의 audit 카드와 같은 처방).
  const [items, setItems] = useState(null);
  const [counts, setCounts] = useState(null);
  const [nextCursor, setNextCursor] = useState(null);
  const [listError, setListError] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [grantUser, setGrantUser] = useState(null);
  const grantTrigger = useRef(null);

  const closeGrant = () => {
    setGrantUser(null);
  };

  useEffect(() => {
    if (!grantUser) grantTrigger.current?.focus();
  }, [grantUser]);

  const load = useCallback(() => {
    setItems(null);
    setNextCursor(null);
    setListError(null);
    adminListUsers({ q: query || undefined, origin: origin || undefined })
      .then((d) => {
        setItems(d.items);
        setNextCursor(d.nextCursor || null);
        // counts 는 첫 페이지에만 실린다. 없으면 이전 값을 유지한다(칩 숫자가 깜빡이지 않게).
        if (d.counts) setCounts(d.counts);
      })
      .catch((e) => setListError(e.message || '사용자 목록을 불러오지 못했어요.'));
  }, [query, origin]);

  useEffect(() => { load(); }, [load]);

  const loadMore = () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    adminListUsers({ q: query || undefined, origin: origin || undefined, cursor: nextCursor })
      .then((d) => {
        // 이어 붙인다 — 다시 그리면 지금까지 훑어 내린 위치를 잃는다.
        setItems((prev) => [...(prev || []), ...d.items]);
        setNextCursor(d.nextCursor || null);
      })
      .catch((e) => setListError(e.message || '더 불러오지 못했어요.'))
      .finally(() => setLoadingMore(false));
  };

  const submitSearch = () => setQuery(term.trim());

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">사용자</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          FaceMarket 과 Wearless 는 계정을 공유해요. 출처는 그 계정이 처음 들어온 서비스이고,
          둘 다 쓰면 ‘양쪽’이 돼요.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch(); }}
          placeholder="이메일 또는 이름"
          disabled={!!grantUser}
          className="w-64"
        />
        <Button size="sm" onClick={submitSearch} disabled={!!grantUser}>검색</Button>
        {query && (
          <Button size="sm" variant="ghost" disabled={!!grantUser} onClick={() => { setTerm(''); setQuery(''); }}>
            검색 해제
          </Button>
        )}
        <span className="mx-1 h-5 w-px bg-border" aria-hidden />
        {ORIGIN_FILTERS.map((f) => (
          <Button
            key={f.value || 'all'}
            size="sm"
            variant={f.value === origin ? 'default' : 'outline'}
            onClick={() => setOrigin(f.value)}
            disabled={!!grantUser}
          >
            {f.label}
            {/* 숫자는 첫 응답 뒤에만 붙는다 — 0 을 미리 그려서 "없다" 고 단정하지 않는다. */}
            {counts && f.countKey && counts[f.countKey] != null && (
              <span className="ml-1.5 text-xs opacity-70">{counts[f.countKey]}</span>
            )}
          </Button>
        ))}
      </div>

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
                  <TableHead>계정</TableHead>
                  <TableHead>이름</TableHead>
                  <TableHead>출처</TableHead>
                  <TableHead>권한</TableHead>
                  <TableHead>가입일</TableHead>
                  <TableHead>최근 로그인</TableHead>
                  <TableHead>크레딧</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((u) => (
                  <Fragment key={u.userId}>
                    <TableRow>
                      {/* 카카오 로그인은 이메일 동의가 선택이라 auth.users.email 이 빈 계정이
                          있다. '-' 대신 그 사실을 말한다. AdminModels 가 같은 함정을 이미
                          겪었다. */}
                      <TableCell className={u.email ? '' : 'text-muted-foreground'}>
                        {u.email || '이메일 없음 (소셜 로그인)'}
                      </TableCell>
                      <TableCell>{u.displayName || '-'}</TableCell>
                      <TableCell><OriginBadge origin={u.appOrigin} /></TableCell>
                      <TableCell className="text-muted-foreground">
                        {u.role === 'admin' ? <Badge variant="destructive">관리자</Badge> : '일반'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{day(u.createdAt)}</TableCell>
                      <TableCell className="text-muted-foreground">{day(u.lastSignInAt)}</TableCell>
                      <TableCell>
                        <Button size="sm" variant="outline" disabled={!!grantUser} onClick={(event) => {
                          grantTrigger.current = event.currentTarget;
                          setGrantUser(u);
                        }}>크레딧 지급</Button>
                      </TableCell>
                    </TableRow>
                    {grantUser?.userId === u.userId && (
                      <TableRow>
                        <TableCell colSpan={7}>
                          <CreditGrantForm user={grantUser} onClose={closeGrant} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
                {items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      결과 없음
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {nextCursor && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? '불러오는 중…' : '더 보기'}
          </Button>
        </div>
      )}
    </div>
  );
}
