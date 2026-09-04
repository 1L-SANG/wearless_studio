/* 관리자 관리 — 이메일로 찾아 권한을 켜고 끈다 + 최근 기록.

   버튼 비활성은 안내일 뿐이고 판정은 서버가 한다(자기 강등·최후 관리자·미가입). UI 가
   막는 것에 기대면, 두 관리자가 동시에 서로를 내리는 경합을 프런트는 볼 수 없다. */
import { useCallback, useEffect, useState } from 'react';
import { adminListAudit, adminListStaff, adminSetRole } from '@/lib/api/facemarket.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import { useToast } from '@/components/ui.jsx';

const ACTION_LABEL = {
  'application.approve': '지원서 승인',
  'application.reject': '지원서 거절',
  'application.resend_email': '결정 메일 재발송',
  'staff.role.grant': '관리자 승격',
  'staff.role.revoke': '관리자 회수',
  'model.suspend': '모델 정지',
  'model.unsuspend': '모델 정지 해제',
  'refund.approve': '환불 승인',
  'refund.reject': '환불 반려',
};

export function AdminStaff() {
  // useToast() 는 { push, dismiss } 를 준다 — AdminApplications.jsx 와 같은 소비 방식.
  // 여기서 서버의 400(자기 강등·최후 관리자)이 토스트로 못 뜨면, 버튼 비활성 우회 경합을
  // 사용자가 알아챌 방법이 없어진다(가드는 안내일 뿐 서버가 진짜 판정이라는 이 화면의 전제).
  const { push } = useToast();
  const { user } = useAuth();
  const [q, setQ] = useState('');
  const [data, setData] = useState(null);
  // 예전엔 이 fetch 가 실패하면 push 토스트만 뜨고(몇 초 뒤 사라짐) data 는 계속 null 로
  // 남았는데, 그 하나의 상태로 화면 렌더 전체를 게이팅하고 있었다 — 그러면 검색 카드까지
  // 영원히 안 보이고, 유일한 복구 방법이 전체 새로고침이었다. 토스트 대신 화면에 남는
  // 에러 상태를 두고, 화면 틀은 항상 그린다 — 로딩·에러는 그 안의 영역에만 국한한다.
  const [dataError, setDataError] = useState(null);
  // null = 아직 한 번도 응답을 못 받음(로딩 중이거나 이번 라운드 전엔 실패해도 계속
  // null). [] = 응답은 왔는데 진짜로 0건. 라운드 2 에서 []로 초기화했다가, 화면 전체
  // 게이팅을 없앤 바로 그 수정 때문에 로딩 중에도 "기록 없음" 이 뜨는 새 결함이 생겼다
  // (전엔 전체 스켈레톤이 이 카드를 가려서 안 보였을 뿐이다) — "아직 안 불러옴" 과
  // "불러왔는데 없음" 을 구분 못 하면 로딩 중에도 "없다" 는 확정적인 거짓 주장을 하게
  // 된다. 이 화면이 막으려는 바로 그 부류의 결함이라 audit 도 admins 카드와 같은 모양
  // (null 스켈레톤 / 에러 / 로드됨)으로 맞춘다.
  const [audit, setAudit] = useState(null);
  // 실패도 빈 배열로 떨어뜨리면 "기록이 없어요" 와 "불러오기 실패" 가 똑같이 보인다 —
  // AdminModels.jsx 목록과 같은 문제, 같은 처방.
  const [auditError, setAuditError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((term) => {
    setDataError(null);
    adminListStaff(term)
      .then(setData)
      .catch((e) => setDataError(e.message || '관리자 목록을 불러오지 못했어요.'));
    setAuditError(null);
    adminListAudit({ limit: 20 })
      .then((d) => setAudit(d.items))
      .catch((e) => setAuditError(e.message || '최근 기록을 불러오지 못했어요.'));
  }, []);

  useEffect(() => { load(undefined); }, [load]);

  const change = async (userId, role) => {
    setBusy(true);
    try {
      await adminSetRole(userId, role);
      load(q.trim() || undefined);
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
    } finally {
      setBusy(false);
    }
  };

  // data 가 없어도(로딩 중이거나 실패했어도) 화면 틀 — 특히 재시도 진입점이 되는 검색
  // 카드 — 은 항상 그린다. admins·matches 는 data 가 없을 때 빈 배열로 안전하게 접는다.
  const admins = data?.admins || [];
  const matches = data?.matches || [];

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>{data ? `관리자 ${admins.length}명` : '관리자'}</CardTitle>
          <CardDescription>
            첫 관리자는 DB 에서 직접 지정해요. 여기서는 이미 가입한 계정만 승격할 수 있어요.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {dataError && (
            <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted-foreground">
              <p>{dataError}</p>
              <Button variant="outline" size="sm" onClick={() => load(q.trim() || undefined)}>다시 시도</Button>
            </div>
          )}
          {!data && !dataError && <Skeleton className="m-5 h-40" />}
          {data && (
            <Table>
              <TableHeader>
                <TableRow><TableHead>이메일</TableHead><TableHead>이름</TableHead><TableHead /></TableRow>
              </TableHeader>
              <TableBody>
                {admins.map((a) => {
                  const isSelf = a.userId === user?.id;
                  const lastAdmin = admins.length <= 1;
                  return (
                    <TableRow key={a.userId}>
                      <TableCell>{a.email || a.userId}</TableCell>
                      <TableCell className="text-muted-foreground">{a.displayName || '-'}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || isSelf || lastAdmin}
                          title={isSelf ? '자기 자신은 내릴 수 없어요' : lastAdmin ? '마지막 관리자는 내릴 수 없어요' : undefined}
                          onClick={() => change(a.userId, 'user')}
                        >
                          관리자 해제
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>계정 찾기</CardTitle>
          <CardDescription>이메일 전체를 정확히 입력해 주세요.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="user@example.com"
              className="w-72"
              onKeyDown={(e) => { if (e.key === 'Enter') load(q.trim() || undefined); }}
            />
            <Button variant="outline" onClick={() => load(q.trim() || undefined)}>검색</Button>
          </div>
          {matches.map((m) => (
            <div key={m.userId} className="flex items-center gap-3">
              <span>{m.email}</span>
              <Badge variant={m.role === 'admin' ? 'default' : 'secondary'}>{m.role}</Badge>
              {m.role !== 'admin' && (
                <Button size="sm" disabled={busy} onClick={() => change(m.userId, 'admin')}>
                  관리자로 올리기
                </Button>
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>최근 기록</CardTitle></CardHeader>
        <CardContent className="p-0">
          {auditError && (
            <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted-foreground">
              <p>{auditError}</p>
              <Button variant="outline" size="sm" onClick={() => load(q.trim() || undefined)}>다시 시도</Button>
            </div>
          )}
          {/* audit 가 null 이면 아직 한 번도 응답을 못 받은 것 — "기록 없음" 은 audit 이
              실제 배열(응답을 받았다는 뜻)일 때만 판정한다. */}
          {!audit && !auditError && <Skeleton className="m-5 h-40" />}
          {audit && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>시각</TableHead><TableHead>한 일</TableHead>
                  <TableHead>대상</TableHead><TableHead>사람</TableHead><TableHead>메모</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {audit.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-muted-foreground">{(row.createdAt || '').slice(0, 16).replace('T', ' ')}</TableCell>
                    <TableCell>{ACTION_LABEL[row.action] || row.action}</TableCell>
                    <TableCell className="text-muted-foreground">{row.targetId || '-'}</TableCell>
                    <TableCell className="text-muted-foreground">{row.actorEmail || '-'}</TableCell>
                    <TableCell className="text-muted-foreground">{row.note || '-'}</TableCell>
                  </TableRow>
                ))}
                {audit.length === 0 && (
                  <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">기록 없음</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
