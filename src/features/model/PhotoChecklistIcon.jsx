import s from './PhotoGuide.module.css';

// 촬영 준비 항목을 설명하는 80px 공통 도해.
export function PhotoChecklistIcon({ kind, className }) {
  return <svg className={className} viewBox="0 0 80 80" aria-hidden="true" focusable="false">
    <rect x="5" y="5" width="66" height="66" rx="21" className={s.iconTint} />
    <ellipse cx="42" cy="65" rx="23" ry="4" fill="currentColor" opacity=".08" />
    <g stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      {kind === 'glasses' && <>
        <path d="m16 36 4-15h10m34 15-4-15H50" fill="none" />
        <rect x="12" y="33" width="23" height="20" rx="8" className={s.iconSurface} />
        <rect x="45" y="33" width="23" height="20" rx="8" className={s.iconSurface} />
        <path d="M35 40q5-4 10 0M18 39h10m23 0h10" fill="none" />
        <path d="m18 64 46-48" className={s.iconAccentStroke} strokeWidth="4" />
      </>}
      {kind === 'person' && <>
        <path d="M21 63v-6c0-23 38-23 38 0v6Z" className={s.iconSurface} />
        <circle cx="40" cy="28" r="13" className={s.iconSurface} />
        <path d="M29 25q11 0 16-8m-12 18q7 5 14 0" fill="none" />
        <circle cx="63" cy="53" r="12" className={s.iconAccentFill} stroke="none" />
        <path d="M63 47v12m-3-9 3-3" fill="none" className={s.iconOnAccentStroke} />
      </>}
      {kind === 'camera' && <>
        <path d="M17 25h12l4-8h16l4 8h10a6 6 0 0 1 6 6v27a6 6 0 0 1-6 6H17a6 6 0 0 1-6-6V31a6 6 0 0 1 6-6Z" className={s.iconSurface} />
        <circle cx="40" cy="44" r="14" className={s.iconTint} />
        <circle cx="40" cy="44" r="8" className={s.iconAccentFill} stroke="none" />
        <path d="m36 41 2-2" className={s.iconOnAccentStroke} />
        <path d="M59 33h3" className={s.iconAccentStroke} strokeWidth="4" />
      </>}
      {kind === 'calendar' && <>
        <circle cx="20" cy="20" r="7" className={s.iconAccentFill} stroke="none" />
        <path d="M20 7v5M7 20h5m16 0h5m-22-9 4 4m10-4-4 4" fill="none" className={s.iconAccentStroke} />
        <circle cx="30" cy="37" r="9" className={s.iconSurface} />
        <path d="M15 64v-5c0-16 30-16 30 0v5Z" className={s.iconSurface} />
        <circle cx="55" cy="37" r="9" className={s.iconSurface} />
        <path d="M40 64v-5c0-16 30-16 30 0v5Z" className={s.iconSurface} />
        <path d="M38 37h9m-4-4 4 4-4 4" fill="none" className={s.iconAccentStroke} strokeWidth="3" />
        <path d="M21 54h18m25 0H46" fill="none" opacity=".35" />
      </>}
    </g>
  </svg>;
}
