/* 사용자 — 셀러(Wearless)와 FaceMarket 가입자를 한 목록에서 **구분해서** 본다.

   두 서비스가 Supabase 프로젝트를 공유해서 profiles 한 테이블에 섞여 있다. 출처는
   profiles.app_origin 한 칸이고, 로그인할 때 서버가 Origin 헤더로 찍는다
   (server/app/app_origin.py). 그래서 '미상'이 존재한다 — 이번 스키마 변경 전에 가입해
   아직 다시 로그인하지 않은 계정이다. '미상'을 셀러로 뭉뚱그리지 않는 이유는 하나다:
   틀린 라벨은 빈 칸보다 나쁘다.

   검색은 타이핑마다가 아니라 Enter·버튼으로 보낸다(AdminModels 와 다른 점). 서버가 이
   목록 열람을 감사 원장에 남기기 때문에, 키 입력마다 요청하면 원장이 한 글자짜리 조회로
   가득 차서 "누가 무엇을 훑었나" 를 못 읽게 된다. */
import { useCallback, useEffect, useState } from 'react';
import { adminListUsers } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
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

const day = (iso) => (iso ? iso.slice(0, 10) : '-');

/* null(미상)과 앞으로 늘어날 수 있는 값을 둘 다 다룬다 — 스키마에 여섯 번째 값이 생겨도
   빈 pill 대신 원문자열을 보여준다. */
function OriginBadge({ origin }) {
  if (!origin) {
    return <Badge variant="outline" className="text-muted-foreground">미상</Badge>;
  }
  return <Badge variant={ORIGIN_VARIANT[origin] || 'outline'}>{ORIGIN_LABEL[origin] || origin}</Badge>;
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
          className="w-64"
        />
        <Button size="sm" onClick={submitSearch}>검색</Button>
        {query && (
          <Button size="sm" variant="ghost" onClick={() => { setTerm(''); setQuery(''); }}>
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
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((u) => (
                  <TableRow key={u.userId}>
                    {/* 카카오 로그인은 이메일 동의가 선택이라 auth.users.email 이 빈 계정이
                        있다 — '-' 대신 그 사실을 말한다. AdminModels 가 같은 함정을 이미
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
                  </TableRow>
                ))}
                {items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
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
