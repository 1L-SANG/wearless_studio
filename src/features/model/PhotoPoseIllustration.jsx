import { PHOTO_GUIDE_ASSETS } from './photoGuideAssets.js';

// 생성한 비트맵에서 해당 칸만 보여줘요. 같은 파일은 브라우저가 한 번만 받아요.
export function PhotoPoseIllustration({ slot, className }) {
  const { src, column, row } = PHOTO_GUIDE_ASSETS[slot.key];
  return <svg className={className} viewBox={`${column} ${row} 1 1`} preserveAspectRatio="xMidYMid slice" overflow="hidden" role="img" aria-label={`${slot.n}번 ${slot.title} 촬영 예시`} focusable="false" data-photo-example={slot.key}>
    <image href={src} x="0" y="0" width="3" height="3" />
  </svg>;
}
