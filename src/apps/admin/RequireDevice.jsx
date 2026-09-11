// src/apps/admin/RequireDevice.jsx
/* 관리자 콘솔 기기 게이트의 화면 쪽.

   서버 가드(admin_guard.require_admin)가 진짜 판정이다. 이 컴포넌트는 그 판정을 미리 물어
   (GET /admin/devices/me) 사람이 이해할 화면을 그린다 — 등록 / 대기 / 회수 / 비관리자.
   gate 가 enforce 가 아니면(shadow·off) 서버가 막지 않으므로 화면도 막지 않는다. 단 토큰이
   없으면 shadow 에서도 등록은 시킨다 — 그게 부트스트랩이다(설계 §9: shadow 배포 → 서로 승인 →
   enforce). off 면 등록조차 시키지 않는다.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6.4 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { adminDeviceMe, adminRegisterDevice } from '@/lib/api/facemarket.js';
import {
  DEVICE_REJECTED_EVENT, clearDeviceToken, defaultDeviceLabel, readDeviceToken, writeDeviceToken,
} from '@/lib/adminDevice.js';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';

const POLL_MS = 10_000;

export function RequireDevice() {
  // phase: loading | pass | register | pending | revoked | forbidden | error
  const [phase, setPhase] = useState('loading');
  const [me, setMe] = useState(null);          // 마지막 /me 응답 { status, deviceId, label, gate }
  const [error, setError] = useState(null);
  const [label, setLabel] = useState(() => defaultDeviceLabel());
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const alive = useRef(true);
  // 늦게 도착한 옛 응답이 새 상태를 덮어쓰지 않게(폴링·지금확인·재등록 경합) — 요청마다
  // 순번을 매기고, 응답이 왔을 때 그 사이 더 최신 요청이 없었는지 확인한다.
  const checkSeq = useRef(0);

  const check = useCallback(async () => {
    const seq = ++checkSeq.current;
    setChecking(true);
    setError(null);
    let res;
    try {
      res = await adminDeviceMe();
    } catch (e) {
      if (alive.current && seq === checkSeq.current) setChecking(false);
      if (!alive.current || seq !== checkSeq.current) return;
      if (e?.status === 403 && e?.code === 'forbidden') { setPhase('forbidden'); return; }
      setError(e.message || '기기 상태를 확인하지 못했어요.');
      setPhase('error');
      return;
    }
    if (alive.current && seq === checkSeq.current) setChecking(false);
    if (!alive.current || seq !== checkSeq.current) return;
    setMe(res);
    const token = readDeviceToken();
    if (res.gate === 'off') { setPhase('pass'); return; }
    if (!token) { setPhase('register'); return; }
    if (res.gate !== 'enforce') { setPhase('pass'); return; }
    if (res.status === 'approved') { setPhase('pass'); return; }
    if (res.status === 'pending') { setPhase('pending'); return; }
    if (res.status === 'revoked') { setPhase('revoked'); return; }
    if (res.status === 'unknown') {
      // 스토리지에 남의 토큰이나 쓰레기가 있다. 지우고 새로 등록시킨다.
      clearDeviceToken();
      setPhase('register');
      return;
    }
    setError(`알 수 없는 기기 상태예요: ${res.status}`);
    setPhase('error');
  }, []);

  useEffect(() => {
    alive.current = true;
    check();
    return () => { alive.current = false; };
  }, [check]);

  // 대기 중일 때만 폴링 — 다른 관리자가 승인하면 새로고침 없이 콘솔로 넘어간다.
  useEffect(() => {
    if (phase !== 'pending') return undefined;
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      check();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [phase, check]);

  // 콘솔 안에서 어떤 요청이든 device_* 403 을 받으면(열어 둔 탭에서 회수됨 등) 상태를 다시 본다.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onRejected = () => { check(); };
    window.addEventListener(DEVICE_REJECTED_EVENT, onRejected);
    return () => window.removeEventListener(DEVICE_REJECTED_EVENT, onRejected);
  }, [check]);

  const register = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await adminRegisterDevice({
        label: label.trim() || null,
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : null,
      });
      if (!alive.current) return; // 응답 오기 전에 언마운트됐으면 스토리지도 건드리지 않는다
      writeDeviceToken(res.token);
      await check();
    } catch (e) {
      if (!alive.current) return;
      if (e?.code === 'too_many_pending') {
        setError('승인 대기 중인 요청이 너무 많아요. 다른 관리자에게 기존 요청을 정리해 달라고 하세요.');
      } else {
        setError(e.message || '기기 등록에 실패했어요.');
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const reset = () => {
    // 지금 날아가고 있는 옛 check() 응답이 재등록 화면을 다시 대기/회수로 덮어쓰지 않게 순번을 올린다.
    checkSeq.current += 1;
    setChecking(false);
    clearDeviceToken();
    setMe(null);
    setError(null);
    setPhase('register');
  };

  if (phase === 'pass') return <Outlet />;
  if (phase === 'loading') return <div className="route-loading">기기 확인 중이에요</div>;

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
      {phase === 'forbidden' && (
        <Card>
          <CardHeader>
            <CardTitle>관리자만 가능해요</CardTitle>
            <CardDescription>이 계정은 관리자 콘솔을 쓸 수 없어요.</CardDescription>
          </CardHeader>
        </Card>
      )}

      {phase === 'error' && (
        <Card>
          <CardHeader>
            <CardTitle>기기 상태를 확인하지 못했어요</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" size="sm" onClick={check}>다시 시도</Button>
          </CardContent>
        </Card>
      )}

      {phase === 'register' && (
        <Card>
          <CardHeader>
            <CardTitle>이 기기를 등록해요</CardTitle>
            <CardDescription>
              관리자 콘솔은 승인된 기기에서만 쓸 수 있어요. 등록하면 다른 관리자가 승인해야 열려요.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <label className="text-sm text-muted-foreground" htmlFor="admin-device-label">기기 이름</label>
            <Input
              id="admin-device-label"
              value={label}
              maxLength={60}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="예: 회사 맥북 · Chrome"
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button disabled={busy} onClick={register}>{busy ? '요청 중…' : '승인 요청'}</Button>
          </CardContent>
        </Card>
      )}

      {phase === 'pending' && (
        <Card>
          <CardHeader>
            <CardTitle>승인 대기 중이에요</CardTitle>
            <CardDescription>
              다른 관리자가 승인해야 해요. 승인되면 이 화면이 자동으로 콘솔로 바뀌어요.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div><span className="text-muted-foreground">기기 이름</span> · {me?.label || '-'}</div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={check} disabled={checking}>지금 확인</Button>
              <Button variant="ghost" size="sm" onClick={reset}>다른 이름으로 다시 등록</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {phase === 'revoked' && (
        <Card>
          <CardHeader>
            <CardTitle>이 기기는 회수됐어요</CardTitle>
            <CardDescription>다시 쓰려면 새로 등록하고 승인을 받아야 해요.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button size="sm" onClick={reset}>새로 등록 요청</Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
