import { LandingShell } from '../LandingShell.jsx';
import { PhotoGuide } from '../../model/PhotoGuide.jsx';
import s from '../FacemarketLanding.module.css';
import guide from '../../model/PhotoGuide.module.css';

export function PhotoGuidePage() {
  return <LandingShell title="등록 사진 18장 촬영 가이드 | FaceMarket" description="밝은 야외에서 약 15분. 그늘 9장과 햇빛에서 3장씩, 등록 사진 18장을 준비해요.">
    {({ ctaLabel, onPrimary }) => <><PhotoGuide /><div className={guide.guideAction}><button className={s.heroCta} type="button" onClick={onPrimary}>{ctaLabel}</button></div></>}
  </LandingShell>;
}
