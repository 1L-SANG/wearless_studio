import { Camera, CloudSun, Clock3, Sun, Users } from 'lucide-react';
import { SLOTS, PHOTO_GROUPS, SHOOT_RULES, REGISTRATION_PHOTO_COUNT, SHOOTING_TIME_MINUTES } from './registerSlots.js';
import { PhotoPoseIllustration } from './PhotoPoseIllustration.jsx';
import { PhotoChecklistIcon } from './PhotoChecklistIcon.jsx';
import s from './PhotoGuide.module.css';

export function PhotoPreparation() {
  return <div className={s.preparation}>
    <ul className={s.facts} aria-label="촬영 준비물과 소요 시간">
      <li><Camera size={17} aria-hidden="true" />총 {REGISTRATION_PHOTO_COUNT}장</li>
      <li><Clock3 size={17} aria-hidden="true" />약 {SHOOTING_TIME_MINUTES}분</li>
      <li><Users size={17} aria-hidden="true" />도와줄 사람 1명</li>
      <li><Sun size={17} aria-hidden="true" />밝은 야외</li>
    </ul>
    <div className={s.framing}>
      <figure className={s.frameExample}>
        <PhotoPoseIllustration slot={SLOTS[0]} className={s.framingArt} />
        <figcaption>정면 · 무표정 예시</figcaption>
      </figure>
      <div><h2>사진 구도</h2>
        <p>머리부터 가슴 위까지, 세로로 찍어요.</p>
        <p className={s.framingNote}>카메라는 눈높이에 맞춰 주세요.</p>
      </div>
    </div>
  </div>;
}

export function ReferencePhotoNotice() {
  return <aside className={s.referenceNote} aria-label="기준 사진 촬영 주의사항">
    <strong>기준 사진 재촬영</strong>
    <p>4~6번은 같은 자리·조명에서, 고개를 고정해 찍어요.</p>
  </aside>;
}

export function ShootChecklist() {
  return <section className={s.checkSection} aria-labelledby="shoot-check-title">
      <h2 id="shoot-check-title">찍기 전, 확인해요</h2>
      <ul className={s.checklist}>{SHOOT_RULES.map((rule) =>
        <li key={rule.title}>
          <PhotoChecklistIcon className={s.checkIcon} kind={rule.icon} />
          <span><strong>{rule.title}</strong><small>{rule.body}</small></span>
        </li>
      )}</ul>
    </section>;
}

export function PhotoGuide() {
  return <div className={s.guide}>
    <header className={s.header}><p className={s.eyebrow}>FaceMarket · 등록 준비</p>
      <h1>사진 18장 촬영 가이드</h1>
      <p>밝은 야외에서, 아래 번호대로 찍으면 돼요.</p>
    </header>
    <ShootChecklist />
    <PhotoPreparation />
    <p className={s.exampleNote}>자세는 그림처럼, 머리는 평소처럼 찍어요.</p>
    <nav className={s.jumpNav} aria-label="촬영 단계 바로가기">{PHOTO_GROUPS.map((group, index) =>
      <a href={`#shoot-${group.id}`} key={group.id}><span>{index + 1}</span>{group.title}<b>{SLOTS.filter((slot) => slot.group === group.id).length}장</b></a>
    )}</nav>
    {PHOTO_GROUPS.map((group, index) => {
      const slots = SLOTS.filter((slot) => slot.group === group.id);
      return <section className={s.shootSection} id={`shoot-${group.id}`} key={group.id} aria-labelledby={`shoot-title-${group.id}`}>
        <div className={s.shootHeading}><span className={s.sectionNumber}>{String(index + 1).padStart(2, '0')}</span>
          <div><p className={s.eyebrow}>{group.badge} · {slots[0].n}~{slots.at(-1).n}번</p><h2 id={`shoot-title-${group.id}`}>{group.action} {slots.length}장</h2><p>{group.note}</p>{group.cloudy && <p className={s.cloudyTip}><CloudSun size={16} aria-hidden="true" /><span><strong>흐리거나 실내라면 {group.cloudy[0]}</strong>{group.cloudy.slice(1).map((line) => <span key={line} className={s.cloudyCheck}>{line}</span>)}</span></p>}</div>
        </div>
        <ol className={s.shotGrid} start={slots[0].n}>{slots.map((slot) => <li key={slot.key}>
          <div className={s.shotArt}><span className={s.shotNumber}>{String(slot.n).padStart(2, '0')}</span><PhotoPoseIllustration className={s.pose} slot={slot} /></div>
          <div className={s.shotCopy}><h3>{slot.title}</h3><p>{slot.hint}</p></div>
        </li>)}</ol>
      </section>;
    })}
    <p className={s.finishNote}>촬영 후 번호에 맞춰 올려 주세요. 나중에 이어서 등록해도 돼요.</p>
  </div>;
}
