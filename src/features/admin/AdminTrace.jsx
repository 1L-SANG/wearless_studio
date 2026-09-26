/* 출처 추적 — 쇼핑몰에서 발견한 이미지를 올리면 어느 배포본·셀러·모델·라이선스에서 나왔는지
   후보를 보여준다(2026-09-26). 서버가 워터마크를 먼저 읽고, 없으면 지문(pHash)으로 찾는다.

   올린 이미지는 서버에 저장되지 않는다(감사 원장에는 해시 앞자리와 결과 요약만 남는다).
   셀러는 마스킹된 이메일·이름으로만 보인다 — 연락이 필요하면 사용자 화면에서 셀러 id 로 찾는다. */
import { useRef, useState } from 'react';
import { adminTraceImage } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/admin-ui/table.jsx';
import {
  confidenceView, evidenceText, licenseStatusLabel, targetLabel, validateTraceFile, watermarkView,
} from './adminTrace.js';

const day = value => (value ? String(value).slice(0, 10) : '-');

export function AdminTrace() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const running = useRef(false);

  const pick = event => {
    const next = event.target.files?.[0] || null;
    setFile(next);
    setError(next ? validateTraceFile(next) : '');
    setResult(null);
  };

  const run = async () => {
    if (running.current) return;
    const invalid = validateTraceFile(file);
    if (invalid) { setError(invalid); return; }
    running.current = true;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      setResult(await adminTraceImage(file));
    } catch (e) {
      setError(e.message || '출처를 추적하지 못했어요.');
    } finally {
      running.current = false;
      setBusy(false);
    }
  };

  const wm = result ? watermarkView(result.watermark) : null;
  const candidates = result?.candidates || [];

  return <div className="flex min-w-0 flex-col gap-5">
    <div>
      <h1 className="text-lg font-semibold tracking-tight">출처 추적</h1>
      <p className="mt-1 text-sm text-muted-foreground">쇼핑몰에서 발견한 이미지를 올리면 배포본에 박힌 워터마크와 이미지 지문으로 어느 셀러·모델·라이선스에서 나왔는지 찾아요.</p>
      <p className="mt-1 text-xs text-muted-foreground">상세페이지 이미지 영역만, 좌우를 자르지 말고 전체 폭으로 올려 주세요. 세로 800px 이상이면 조각이어도 돼요. 올린 이미지는 저장하지 않아요.</p>
    </div>
    <div className="flex max-w-xl flex-col gap-2 sm:flex-row sm:items-end">
      <label className="flex flex-1 flex-col gap-1 text-sm">발견한 이미지
        <Input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={pick} />
      </label>
      <Button disabled={busy || !file} onClick={run}>{busy ? '추적 중…' : '추적하기'}</Button>
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {busy && <Skeleton className="h-40" aria-label="출처를 추적하고 있어요" />}
    {result && <>
      <Card className="min-w-0"><CardContent className="flex flex-col gap-1 p-4">
        <span className={wm.tone === 'ok' ? 'font-medium' : wm.tone === 'warn' ? 'font-medium text-amber-700' : 'font-medium text-muted-foreground'}>{wm.title}</span>
        <span className="text-sm text-muted-foreground">{wm.detail}</span>
        <span className="text-xs text-muted-foreground">이미지 {result.image?.width}×{result.image?.height} · 지문 {result.stats?.fingerprintsScanned ?? 0}개와 비교 · {result.stats?.elapsedMs ?? 0}ms</span>
      </CardContent></Card>
      <Card className="min-w-0"><CardContent className="p-0">
        <Table><TableHeader><TableRow>
          <TableHead>신뢰도</TableHead><TableHead>대상</TableHead><TableHead>셀러</TableHead><TableHead>모델</TableHead><TableHead>라이선스</TableHead><TableHead>근거</TableHead><TableHead>확인</TableHead>
        </TableRow></TableHeader>
        <TableBody>{candidates.map(item => {
          const conf = confidenceView(item.confidence);
          const key = item.publicationId || item.outputRecordId;
          return <TableRow key={key}>
            <TableCell><div className="flex min-w-28 flex-col items-start gap-1"><Badge variant={conf.variant}>{conf.label}</Badge><span className="text-xs text-muted-foreground">{conf.hint}</span></div></TableCell>
            <TableCell><div className="flex flex-col"><span>{targetLabel(item)}</span><span className="text-xs text-muted-foreground">{item.projectTitle || '(제목 없음)'} · {day(item.createdAt)}</span><span className="text-xs text-muted-foreground">{key}</span></div></TableCell>
            <TableCell><div className="flex flex-col"><span>{item.seller?.nameMasked || '-'}</span><span className="text-xs text-muted-foreground">{item.seller?.emailMasked || '-'}</span><span className="text-xs text-muted-foreground">{item.seller?.id || ''}</span></div></TableCell>
            <TableCell>{item.model?.displayName || item.model?.id || '-'}</TableCell>
            <TableCell><div className="flex flex-col"><span>{licenseStatusLabel(item.license?.status)}</span><span className="text-xs text-muted-foreground">{item.license?.id || ''}</span>{item.revokedAt && <span className="text-xs text-destructive">배포본 철회 {day(item.revokedAt)}</span>}</div></TableCell>
            <TableCell className="text-sm">{evidenceText(item.evidence)}</TableCell>
            <TableCell>{item.verifyUrl ? <a className="text-sm underline" href={item.verifyUrl} target="_blank" rel="noreferrer">검증 페이지</a> : <span className="text-xs text-muted-foreground">-</span>}</TableCell>
          </TableRow>;
        })}{candidates.length === 0 && <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">일치하는 배포본·컷을 찾지 못했어요. FaceMarket 배포본이 아니거나 너무 많이 바뀐 이미지예요.</TableCell></TableRow>}</TableBody>
        </Table>
      </CardContent></Card>
    </>}
  </div>;
}
