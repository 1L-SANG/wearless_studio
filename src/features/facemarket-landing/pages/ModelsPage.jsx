/* 모델 둘러보기 — 상단바 첫 항목의 목적지(/models).
   등록된 셀러와 모델만 목록을 본다(2026-09-23 오너 결정). 판정은 서버가 한다
   (GET /v1/facemarket/catalog-access, server/app/facemarket_catalog_access.py). 비로그인과
   자격 없는 로그인 계정은 MembersOnlyGate 안내를 본다. 목록 API 도 같은 판정으로 막혀 있다.

   ⚠️ 지금 보이는 건 **가상 모델 예시**다(browseModels.js). 실데이터를 붙일 때
   등록된 모델의 얼굴을 목록으로 걸 수 없다 — PRD §10 하드룰 1: 얼굴 사진은 공개 주소를
   갖지 않고, 권한이 확인된 요청에만 그때그때 열린다. 예외는 모델이 직접 올린 대표 이미지
   하나뿐이고 그것도 1시간짜리 서명 주소다. 그러니 실제로 걸 수 있는 건 대표 이미지를 올린
   모델로 한정한 목록이고, '누가 등록했다'는 사실 자체를 공개할지부터 결정해야 한다. */
import { useEffect, useState } from 'react';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { getCatalogAccess } from '@/lib/api/facemarket.js';
import { LandingShell } from '../LandingShell.jsx';
import { BrowseSection } from '../sections/BrowseSection.jsx';
import { MembersOnlyGate } from '../sections/MembersOnlyGate.jsx';
import gate from '../sections/MembersOnlyGate.module.css';

const TITLE = '모델 둘러보기 — FaceMarket';
const DESCRIPTION =
  '얼굴과 신체 사이즈, 그리고 그 얼굴을 어떤 조건으로 쓸 수 있는지 함께 봅니다. '
  + '지금 보이는 목록은 전부 가상 모델 예시입니다.';

// 세션과 자격을 확인하는 동안에는 둘 다 그리지 않는다. 볼 수 있는 사람에게 안내가 비치면 안 된다.
// 자격 조회가 실패하면 안내를 보여 준다. 목록 API 도 같은 판정이라 목록을 그려도 비어 있기 때문이다.
export function ModelsBody() {
  const { session, loading } = useAuth();
  const userId = session?.user?.id ?? null;
  const [access, setAccess] = useState({ userId: null, allowed: false });
  useEffect(() => {
    if (!userId) return undefined;
    let alive = true;
    getCatalogAccess()
      .then((result) => { if (alive) setAccess({ userId, allowed: result?.allowed === true }); })
      .catch(() => { if (alive) setAccess({ userId, allowed: false }); });
    return () => { alive = false; };
  }, [userId]);
  if (loading) return <div className={gate.pending} aria-busy="true" />;
  if (!userId) return <MembersOnlyGate />;
  if (access.userId !== userId) return <div className={gate.pending} aria-busy="true" />;
  return access.allowed ? <BrowseSection /> : <MembersOnlyGate />;
}

export function ModelsPage() {
  return (
    <LandingShell description={DESCRIPTION} surface="plain" title={TITLE}>
      {() => <ModelsBody />}
    </LandingShell>
  );
}
