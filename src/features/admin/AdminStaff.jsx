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
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback((term) => {
    adminListStaff(term).then(setData).catch((e) => push?.(e.message, { icon: 'alertCircle' }));
    adminListAudit({ limit: 20 }).then((d) => setAudit(d.items)).catch(() => setAudit([]));
  }, [push]);

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

  if (!data) return <Skeleton className="h-64" />;

  const { admins, matches } = data;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>관리자 {admins.length}명</CardTitle>
          <CardDescription>
            첫 관리자는 DB 에서 직접 지정해요. 여기서는 이미 가입한 계정만 승격할 수 있어요.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
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
        </CardContent>
      </Card>
    </div>
  );
}
