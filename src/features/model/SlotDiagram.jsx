/* 등록 사진 칸 그림 — 사진 파일 없이 도형만 그려요(currentColor 만 씁니다).

   RegisterIllustration 은 각도(정면/3:4/옆)만 구분해요. 그래서 정면 네 칸(무표정·미소·
   시선 왼쪽·시선 오른쪽)이 똑같이 보이고, 'back' 을 몰라서 뒷모습 칸에도 앞모습이 나왔어요.
   여기서는 앞모습은 **눈동자와 입**으로, 옆·뒤는 **위에서 본 머리(코 방향)** 로 구분해요.

   cut 값은 server/app/facemarket_photos.py 의 CUT_LABELS 키와 같아요
   (front · smile · 34 · front2 · gaze_left · gaze_right · side · side_right · back).
   RegisterIllustration 은 지우지 않아요 — 체형 고르기 화면이 그걸 그대로 씁니다. */

const STROKE = { fill: 'none', stroke: 'currentColor', strokeWidth: 2.4, strokeLinecap: 'round' };

// 앞에서 본 얼굴 — 눈동자 위치와 입 모양만 바꿔요.
function FaceFront({ pupil = 0, smile = false, repeat = false }) {
  return <>
    <ellipse cx="60" cy="58" rx="27" ry="34" {...STROKE} />
    <path d="M33 48c4-16 12-24 27-24s23 8 27 24" {...STROKE} />
    <path d="M44 100c3-9 8-13 16-13s13 4 16 13" {...STROKE} />
    {/* 눈 테두리 — 이게 없으면 눈동자를 옮겨도 '시선'으로 안 읽혀요(1·5·6번이 같아 보여요). */}
    <ellipse cx="50" cy="56" rx="7" ry="4.5" {...STROKE} strokeWidth="1.8" />
    <ellipse cx="70" cy="56" rx="7" ry="4.5" {...STROKE} strokeWidth="1.8" />
    <circle cx={50 + pupil} cy="56" r="2.6" fill="currentColor" />
    <circle cx={70 + pupil} cy="56" r="2.6" fill="currentColor" />
    {smile
      ? <path d="M49 74c4 6 18 6 22 0" {...STROKE} />
      : <line x1="50" y1="76" x2="70" y2="76" {...STROKE} />}
    {/* 1번과 4번은 같은 포즈예요. 그림까지 같으면 왜 두 번 찍는지 알 수 없어서 '한 번 더'
        표시를 답니다 — 이 한 조각만 원본 도형에 더한 것입니다. */}
    {repeat && <>
      <circle cx="96" cy="24" r="13" {...STROKE} strokeWidth="2" />
      <text x="96" y="29" textAnchor="middle" fontSize="15" fill="currentColor">2</text>
    </>}
  </>;
}

// 위에서 본 머리 — 삼각형이 코예요. deg 0 = 찍는 사람(아래)을 봄, ±90 = 옆모습, 180 = 뒤통수.
function HeadTop({ deg = 0, note }) {
  return <>
    <circle cx="60" cy="62" r="26" {...STROKE} />
    <g transform={`rotate(${deg} 60 62)`}>
      <polygon points="60,94 53,82 67,82" fill="currentColor" />
    </g>
    {deg === 180 && <path d="M52 52c6-4 12-4 18 0" {...STROKE} strokeWidth="2" opacity="0.6" />}
    {/* 찍는 사람 */}
    <circle cx="60" cy="108" r="6" {...STROKE} strokeWidth="2" />
    {note && <text x="60" y="20" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.6">{note}</text>}
  </>;
}

export function SlotDiagram({ cut = 'front', className }) {
  const body = {
    front: <FaceFront />,
    front2: <FaceFront repeat />,
    smile: <FaceFront smile />,
    gaze_left: <FaceFront pupil={-4} />,
    gaze_right: <FaceFront pupil={4} />,
    34: <HeadTop deg={-35} note="30~40도" />,
    side: <HeadTop deg={-90} note="끝까지" />,
    side_right: <HeadTop deg={90} note="끝까지" />,
    back: <HeadTop deg={180} />,
  }[cut] || <FaceFront />;
  return <svg className={className} viewBox="0 0 120 120" aria-hidden="true">{body}</svg>;
}

// 사진 크기 안내 — 좋은 구도 / 너무 멀리 / 머리 잘림. "얼굴 폭이 사진 가로의 1/4" 은
// 글로만 읽으면 가늠이 안 돼서, 서버가 "얼굴이 작아요"로 반려하는 기준을 그림으로 둬요.
export function FramingDiagram({ className }) {
  return <svg className={className} viewBox="0 0 460 250" aria-hidden="true">
    <g transform="translate(20,14)">
      <rect x="0" y="0" width="120" height="180" rx="10" {...STROKE} strokeWidth="2" />
      <circle cx="60" cy="70" r="28" {...STROKE} />
      <path d="M22 180c4-31 15-46 38-46s34 15 38 46" {...STROKE} />
      <line x1="32" y1="112" x2="88" y2="112" stroke="currentColor" strokeWidth="2" />
      <line x1="32" y1="107" x2="32" y2="117" stroke="currentColor" strokeWidth="2" />
      <line x1="88" y1="107" x2="88" y2="117" stroke="currentColor" strokeWidth="2" />
      <text x="60" y="130" textAnchor="middle" fontSize="11" fill="currentColor">가로의 1/4</text>
      <text x="60" y="206" textAnchor="middle" fontSize="12" fill="currentColor">이렇게</text>
    </g>
    <g transform="translate(170,14)" opacity="0.55">
      <rect x="0" y="0" width="120" height="180" rx="10" {...STROKE} strokeWidth="2" />
      <circle cx="60" cy="58" r="12" {...STROKE} strokeWidth="2" />
      <path d="M44 124c2-26 7-39 16-39s14 13 16 39" {...STROKE} strokeWidth="2" />
      <path d="M48 124h24v56H48z" {...STROKE} strokeWidth="2" />
      <text x="60" y="206" textAnchor="middle" fontSize="12" fill="currentColor">너무 멀어요</text>
    </g>
    <g transform="translate(320,14)" opacity="0.55">
      <rect x="0" y="0" width="120" height="180" rx="10" {...STROKE} strokeWidth="2" />
      {/* 프레임 위쪽으로 잘려 나간 머리 — 원을 다 그리면 잘린 데가 없어 설명과 그림이 달라져요. */}
      <path d="M7.7 0A56 56 0 1 0 112.3 0" {...STROKE} />
      <text x="60" y="206" textAnchor="middle" fontSize="12" fill="currentColor">머리가 잘렸어요</text>
    </g>
  </svg>;
}

// 자리 이동 안내 — 1단계 그늘, 2~4단계는 몸을 90도씩. 해 이름 대신 **서는 자리와 도는 방향**
// 을 보여 줘요(2026-09-16 대표 결정).
//
// 해를 위에 고정하고 **사람이 돌아요**(SunDiagram 은 반대로 사람을 고정하고 해를 옮겨요).
// 2단계는 해를 보고 서고, 오른쪽으로 두 번 돌면 4단계에서 해를 등져요 — 문구가 말하는
// 그대로예요. 찍는 사람은 늘 얼굴 정면이라 머리와 **같은 그룹**으로 묶어 함께 돌려요:
// 따로 두면 2·3단계가 뒤통수를 찍는 그림이 돼요.
export function StepDiagram({ step = 1, className }) {
  // 0 = 찍는 사람(아래)을 보는 자리 = 해를 등진 4단계. 거기서 거꾸로 세어요.
  const deg = { 1: 180, 2: 180, 3: 270, 4: 0 }[step] ?? 180;
  return <svg className={className} viewBox="0 0 120 120" aria-hidden="true">
    {step === 1
      ? <rect x="8" y="4" width="104" height="22" rx="10" fill="currentColor" opacity="0.12" />
      : <g transform="translate(60,16)">
          <circle r="9" fill="currentColor" opacity="0.85" />
          {[0, 45, 90, 135].map((d) => <line key={d} x1="-14" y1="0" x2="14" y2="0" transform={`rotate(${d})`} stroke="currentColor" strokeWidth="2" opacity="0.5" />)}
        </g>}
    <g transform={`rotate(${deg} 60 74)`}>
      <circle cx="60" cy="74" r="17" {...STROKE} strokeWidth="2.2" />
      <polygon points="60,96 54,87 66,87" fill="currentColor" />
      {/* 찍는 사람 */}
      <circle cx="60" cy="110" r="5" {...STROKE} strokeWidth="2" />
    </g>
  </svg>;
}
