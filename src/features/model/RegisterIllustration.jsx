// 시안 HTML의 도형을 JSX로 옮겼어요. 사진 파일은 사용하지 않아요.
export function RegisterIllustration({ framing = 'full', angle = 'front', width = 1, className }) {
  const side = ['left', 'right'].includes(angle);
  const turned = angle.includes('45');
  const flip = angle === 'right' || angle === 'right45';
  const crop = framing === 'face' ? '45 18 90 120' : framing === 'torso' ? '20 15 140 187' : '0 0 180 240';
  return <svg className={className} viewBox={crop} aria-hidden="true">
    <g transform={flip ? 'translate(180 0) scale(-1 1)' : undefined}>
      <g transform={`translate(90 0) scale(${width} 1) translate(-90 0)`}>
        {framing === 'face' ? <path d="M80 83h20l8 15c20 4 33 19 38 42H34c5-23 18-38 38-42Z" /> : <>
          {side ? <><path d="M83 86c-10 5-15 14-15 31l4 42h32l3-46c0-15-8-24-17-27Z" /><path d="m89 96 12 47-7 22" fill="none" strokeWidth="9" /></> : <path d={`M77 86q13 6 26 0l${turned ? 15 : 21} 12 9 55-13 4-9-40-2 41H71l-2-41-9 40-13-4 9-55Z`} />}
          {framing === 'full' && <path d={angle === 'pose' ? 'm72 154 35 1 1 32 17 35-11 6-24-39-5-20-3 27-11 33H58l12-39Z' : side ? 'M73 153h29l-2 70 10 4v6H88l-3-56-3 50H66v-7Z' : 'M72 152h36l-4 71 8 4v6H92l-2-60-2 60H68v-6l8-4Z'} />}
        </>}
      </g>
      <g transform={`rotate(${turned ? -7 : angle === 'pose' ? 5 : 0} 90 70)`}>
        {side ? <path d="M87 28c-13 0-20 10-19 23l-7 10 8 4v9c3 6 10 8 17 6v10h16V69c8-11 8-29-1-36-4-3-9-5-14-5Z" /> : turned ? <><path d="M91 26c-14 0-24 11-24 27 0 13 8 25 19 25 13 0 21-12 21-28 0-14-6-24-16-24Z" /><path d="M82 73h16v19H82z" /></> : <><ellipse cx="90" cy="52" rx="21" ry="26" /><path d="M82 73h16v19H82z" /></>}
      </g>
    </g>
  </svg>;
}
