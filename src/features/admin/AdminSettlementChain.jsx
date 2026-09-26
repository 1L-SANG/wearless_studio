/* =============================================================
   관리자 · 체인 검증 (2026-09-26)

   fm_settlements(우리 장부)를 최신순으로 보여 주고, 행마다 [체인 조회]를 누르면 서버가
   OmniOne Chain 의 FaceMarketSettlement.getSettlement 를 그 자리에서 eth_call 로 읽어
   장부 값과 칸마다 나란히 대조한다. 위쪽 판은 컨트랙트의 분배 규칙을 **원문 그대로** 인용한다
   (서버 CONTRACT_RULE_LINES — 테스트가 .sol 원문과 한 글자씩 맞춘다).

   🔴 조회가 실패하면 실패라고만 쓴다. "일치" 배지는 서버 판정(verdict=match)일 때만.
   ============================================================= */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { adminCheckSettlementOnChain, adminListSettlements } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/admin-ui/table.jsx';
import { seoulClock, seoulDateKey } from '@/lib/datetime.js';
import { chainCheckError, chainCheckedLabel, chainFieldValue, chainVerdict, shortHash } from '@/lib/settlementChain.js';

const won = value => `${Number(value || 0).toLocaleString('ko-KR')}원`;
const STATUS = {
  confirmed: { label: '체인 확정', variant: 'secondary' },
  pending: { label: '기록 대기', variant: 'outline' },
  failed: { label: '기록 실패', variant: 'destructive' },
};

function ContractPanel({ contract, totals }) {
  if (!contract) return null;
  return <Card><CardContent className="flex flex-col gap-3 p-5 text-sm">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="font-semibold">분배 규칙 · 컨트랙트 원문</h2>
      <span className="text-xs text-muted-foreground">{contract.ruleSource}</span>
    </div>
    <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs leading-relaxed">{(contract.ruleLines || []).join('\n')}</pre>
    <p>{contract.ruleSummary}</p>
    <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[auto_1fr]">
      <dt className="text-muted-foreground">컨트랙트 주소</dt>
      <dd className="break-all font-mono text-xs">{contract.address || '설정 없음'}</dd>
      <dt className="text-muted-foreground">체인 ID</dt>
      <dd className="font-mono text-xs">{contract.chainId ?? '-'}{contract.connected ? '' : ' · 서버에 체인 연결이 설정되지 않았어요'}</dd>
      {totals && <><dt className="text-muted-foreground">장부 합계</dt>
        <dd>{totals.count}건(체인 확정 {totals.confirmedCount}건) · 총액 {won(totals.totalAmount)} · 모델 몫 {won(totals.modelAmount)}</dd></>}
    </dl>
  </CardContent></Card>;
}

function CheckResult({ state }) {
  if (!state) return null;
  if (state.status === 'loading') return <p className="text-xs text-muted-foreground">체인에 묻는 중…</p>;
  if (state.status === 'error') return <p role="alert" className="text-xs">{state.message}</p>;
  const verdict = chainVerdict(state.result);
  return <div className="flex flex-col gap-2">
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className={verdict?.tone === 'ok'
        ? 'rounded-full bg-emerald-100 px-2.5 py-0.5 font-semibold text-emerald-800'
        : 'rounded-full bg-red-100 px-2.5 py-0.5 font-semibold text-red-800'}>{verdict?.label || '판정 불가'}</span>
      <span className="text-muted-foreground">{chainCheckedLabel(state.result.checkedAt)} · {state.result.method}</span>
    </div>
    <table className="w-full max-w-xl text-xs">
      <thead><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">항목</th><th className="py-1 font-normal">우리 장부(DB)</th><th className="py-1 font-normal">체인(eth_call)</th><th className="py-1 font-normal">대조</th></tr></thead>
      <tbody>{(state.result.fields || []).map(field => <tr key={field.key} className={field.match ? '' : 'text-red-700'}>
        <td className="py-1">{field.label}</td><td className="py-1 font-mono">{chainFieldValue(field, 'db')}</td>
        <td className="py-1 font-mono">{chainFieldValue(field, 'chain')}</td><td className="py-1">{field.match ? '같음' : '다름'}</td>
      </tr>)}</tbody>
    </table>
    <p className="text-xs text-muted-foreground">{verdict?.sentence}</p>
  </div>;
}

export function AdminSettlementChain() {
  const [data, setData] = useState(null);
  const [listError, setListError] = useState('');
  const [checks, setChecks] = useState({});
  const alive = useRef(false);
  const inFlight = useRef(new Set());

  const load = useCallback(async () => {
    setData(null);
    setListError('');
    try {
      const payload = await adminListSettlements({ limit: 100 });
      if (alive.current) setData(payload);
    } catch (error) {
      if (alive.current) setListError(error.message || '정산 장부를 불러오지 못했어요.');
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    return () => { alive.current = false; };
  }, [load]);

  const check = async item => {
    if (inFlight.current.has(item.id)) return;
    inFlight.current.add(item.id);
    setChecks(previous => ({ ...previous, [item.id]: { status: 'loading' } }));
    try {
      const result = await adminCheckSettlementOnChain(item.id);
      if (alive.current) setChecks(previous => ({ ...previous, [item.id]: { status: 'done', result } }));
    } catch (error) {
      if (alive.current) setChecks(previous => ({ ...previous, [item.id]: { status: 'error', message: chainCheckError(error) } }));
    } finally {
      inFlight.current.delete(item.id);
    }
  };

  const items = data?.items;
  return <div className="flex min-w-0 flex-col gap-5">
    <div>
      <h1 className="text-lg font-semibold tracking-tight">체인 검증</h1>
      <p className="mt-1 text-sm text-muted-foreground">정산은 생성 때 OmniOne Chain 에 자동 기록돼요. [체인 조회]는 컨트랙트를 지금 직접 읽어 우리 장부와 한 칸씩 대조해요.</p>
    </div>
    <ContractPanel contract={data?.contract} totals={data?.totals} />
    <Button size="sm" variant="outline" className="self-start" onClick={load}>목록 새로 고침</Button>
    {listError && <p role="alert" className="text-sm">{listError}</p>}
    <Card className="min-w-0"><CardContent className="p-0">
      {!items && !listError && <Skeleton className="h-64" aria-label="정산 장부를 불러오고 있어요" />}
      {items && <Table><TableHeader><TableRow>
        <TableHead>기록 시각</TableHead><TableHead>모델 · 상품</TableHead><TableHead>총액</TableHead>
        <TableHead>모델 / 플랫폼 / 운영</TableHead><TableHead>상태</TableHead><TableHead>트랜잭션</TableHead><TableHead>동작</TableHead>
      </TableRow></TableHeader>
        <TableBody>{items.map(item => {
          const status = STATUS[item.chainStatus] || { label: item.chainStatus, variant: 'outline' };
          const state = checks[item.id];
          return <Fragment key={item.id}>
            <TableRow>
              <TableCell className="whitespace-nowrap">{seoulDateKey(item.createdAt)} {seoulClock(item.createdAt)}</TableCell>
              <TableCell><span className="block">{item.modelName || '-'}</span>
                <span className="block text-xs text-muted-foreground">{item.productName || (item.kind === 'simulation' ? '관리자 시뮬레이션 정산' : '상품명 없음')}</span></TableCell>
              <TableCell className="whitespace-nowrap">{won(item.totalAmount)}</TableCell>
              <TableCell className="whitespace-nowrap text-xs">{won(item.modelAmount)} / {won(item.platformAmount)} / {won(item.opsAmount)}</TableCell>
              <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
              <TableCell><span className="font-mono text-xs" title={item.txHash || ''}>{shortHash(item.txHash)}</span>
                <span className="block text-xs text-muted-foreground">블록 {item.recordedBlock != null ? `#${item.recordedBlock}` : '-'} · 체인 {item.chainId || '-'}</span></TableCell>
              <TableCell><Button size="sm" variant="outline" disabled={state?.status === 'loading'} onClick={() => check(item)}>
                {state ? '다시 조회' : '체인 조회'}</Button></TableCell>
            </TableRow>
            {state && <TableRow><TableCell colSpan={7} className="bg-muted/30"><CheckResult state={state} /></TableCell></TableRow>}
          </Fragment>;
        })}
          {items.length === 0 && <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">아직 정산 기록이 없어요.</TableCell></TableRow>}
        </TableBody>
      </Table>}
    </CardContent></Card>
  </div>;
}
