/* 콘솔 첫 화면 — 손댈 일 → 기간 지표 → 추이·분포.

   순서가 곧 용도다. 관리자가 이 화면을 여는 첫 이유는 "내가 처리해야 할 게 있나"이고,
   숫자 구경은 그다음이다. 큐 카드는 전부 목록 화면으로 이어진다 — 보여주고 끝나면
   결국 다른 화면을 다시 찾아 들어가야 한다. */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { adminOverview } from '@/lib/api/facemarket.js';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Sparkline } from './Sparkline.jsx';

const PERIODS = [7, 30, 90];

const won = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;

function QueueCard({ label, count, to, tone = 'default' }) {
  const idle = !count;
  const body = (
    <Card className={idle ? 'opacity-60' : ''}>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className={`text-2xl ${!idle && tone === 'alert' ? 'text-destructive' : ''}`}>
          {count ?? 0}
        </CardTitle>
      </CardHeader>
    </Card>
  );
  return idle ? body : <Link to={to} className="block">{body}</Link>;
}

function Stat({ label, value }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

export function AdminDashboard() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    adminOverview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || '불러오지 못했어요.'); });
    return () => { alive = false; };
  }, [days]);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>대시보드를 불러오지 못했어요</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => setDays((d) => d)}>다시 시도</Button>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <div className="grid gap-3 sm:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
    );
  }

  const { queue, kpi, series, distribution } = data;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">손댈 일</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <QueueCard label="검토 대기 지원서" count={queue.applicationsUnderReview} to="/applications?status=under_review" />
          <QueueCard label="신분증 대조 실패" count={queue.identityMismatch} to="/applications?status=under_review" tone="alert" />
          <QueueCard label="결정 메일 미발송" count={queue.emailFailed} to="/applications?status=approved" tone="alert" />
          <QueueCard label="환불 요청 대기" count={queue.refundsPending} to="/applications" />
        </div>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">지표</h2>
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <Button key={p} size="sm" variant={p === days ? 'default' : 'outline'} onClick={() => setDays(p)}>
                {p}일
              </Button>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="신규 지원서" value={kpi.applicationsSubmitted} />
          <Stat label="승인 / 거절" value={`${kpi.applicationsApproved} / ${kpi.applicationsRejected}`} />
          <Stat label="라이선스 발급" value={kpi.licensesIssued} />
          <Stat label="크레딧 결제 매출" value={won(kpi.creditRevenueKrw)} />
          <Stat label="정산 금액" value={won(kpi.settlementAmountKrw)} />
          <Stat label="정산 실패" value={kpi.settlementFailed} />
          <Stat label="검증된 모델" value={distribution.models.verified} />
          <Stat label="생체등록 진행 중" value={distribution.enrollments.inFlight} />
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader><CardDescription>일별 지원서</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 지원서" points={series.map((s) => ({ value: s.applications }))} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>일별 라이선스 발급</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 라이선스 발급" points={series.map((s) => ({ value: s.licenses }))} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>일별 정산액</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 정산액" points={series.map((s) => ({ value: s.settlementAmountKrw }))} />
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader><CardDescription>모델 상태</CardDescription></CardHeader>
          <CardContent className="flex gap-6 text-sm">
            <span>대기 {distribution.models.pending}</span>
            <span>검증됨 {distribution.models.verified}</span>
            <span>정지 {distribution.models.suspended}</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>생체등록</CardDescription></CardHeader>
          <CardContent className="flex gap-6 text-sm">
            <span>통과 {distribution.enrollments.passed}</span>
            <span>진행 중 {distribution.enrollments.inFlight}</span>
            <span>실패·만료 {distribution.enrollments.failed}</span>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
