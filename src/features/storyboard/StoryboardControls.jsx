import React, { useState, useEffect } from 'react';
import { Icon } from '@/components/ui.jsx';
import { isSourceBasedDetailRecipe } from '../../lib/detailRecommendations.js';
import { isGenerationCombinationPublic } from '../../lib/generationExamples.js';
import styles from './StoryboardPanels.module.css';

export function UnderlineTabs({ options, value, onChange }) {
  return <div className={`utabs ${styles.underlineTabs}`}>
    {options.map(option => <button type="button" key={option.value} disabled={option.disabled}
      className={`utab${value === option.value ? ' on' : ''}`} aria-pressed={value === option.value}
      title={option.disabled ? option.disabledReason || '이 조건은 아직 서비스에 공개되지 않았어요' : undefined}
      onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}

export function ColorDots({ colorOpts, value, onChange }) {
  return <div className="color-dots">{colorOpts.map(color => <button type="button" key={color.id}
    className={`color-dot${value === color.id ? ' on' : ''}`} title={color.label} aria-label={color.label}
    aria-pressed={value === color.id} onClick={() => onChange(color.id)}>
    <svg className="cd-fill" viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="14" fill={color.hex} /></svg>
  </button>)}</div>;
}

export function ExampleThumb({ example }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setAttempt(0); setFailed(false); }, [example?.thumb]);
  if (failed) return (
    <span className="sb-exthumb-error">썸네일을 불러오지 못했어요
      <span role="button" tabIndex={0} onClick={(event) => {
        event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
      }} onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
        }
      }}>다시 시도</span>
    </span>
  );
  const separator = String(example?.thumb || '').includes('?') ? '&' : '?';
  return <img src={`${example.thumb}${attempt ? `${separator}retry=${attempt}` : ''}`} alt="" onError={() => setFailed(true)} />;
}

export function ShotSegment({
  options, value, onChange, cut, clothingType, gender, isOptionPublished = null,
}) {
  return (
    <div className="seg sb-shot-seg" data-count={options.length}
      data-idx={Math.max(0, options.findIndex((option) => option.value === value))}
      aria-label={cut === 'product' ? '제품컷 형식' : '샷 종류'}>
      {options.map((option) => {
        const published = !option.disabled && (isOptionPublished
          ? isOptionPublished(option.value)
          : (isSourceBasedDetailRecipe({ cutType: cut, shot: option.value, clothingType })
            || isGenerationCombinationPublic({
              cutType: cut, shot: option.value, clothingType, gender,
            })));
        return (
          <button key={option.value} type="button" className={value === option.value ? 'on' : ''}
            disabled={!published} aria-pressed={value === option.value}
            title={!published ? '이 조건은 아직 서비스에 공개되지 않았어요' : undefined}
            onClick={() => onChange(option.value)}>{option.label}</button>
        );
      })}
    </div>
  );
}

export const MINE_SHOT_OPTION = Object.freeze({ value: 'mine', label: '내 이미지' });

export function MineImageTab({ images = [], onImagesChange, onChoose, onPickImage, onUploadStart }) {
  const upload = async () => {
    const finish = onUploadStart?.();
    if (onUploadStart && !finish) return;
    try {
      const picked = await onPickImage?.();
      if (!picked) return;
      if (onChoose) {
        onChoose(picked);
        return;
      }
      onImagesChange?.([...images, picked]);
    } finally {
      finish?.();
    }
  };
  return (
    <div className="sb-mine-tab">
      <p>가지고 있는 이미지를 그대로 넣어요. AI 생성 옵션은 적용되지 않습니다.</p>
      {images.length > 0 && (
        <div className="sb-mine-grid">
          {images.map((image, index) => {
            const src = image?.url || image;
            return (
              <span key={`${src}:${index}`} className={`sb-excell up${onChoose ? ' usable' : ''}`}
                role={onChoose ? 'button' : undefined} tabIndex={onChoose ? 0 : undefined}
                title={onChoose ? '이 이미지로 바꾸기' : undefined}
                onClick={onChoose ? () => onChoose(image) : undefined}
                onKeyDown={onChoose ? (event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault(); onChoose(image);
                  }
                } : undefined}>
                <img src={src} alt="" /><span className="upb">내 사진</span>
                <button type="button" className="rm" aria-label="내 이미지 삭제"
                  onClick={(event) => {
                    event.stopPropagation();
                    onImagesChange?.(images.filter((_, itemIndex) => itemIndex !== index));
                  }}><Icon name="x" size={11} /></button>
              </span>
            );
          })}
        </div>
      )}
      <button type="button" className="ref-upload" onClick={upload}>
        <Icon name="upload" size={16} />로컬에서 이미지 업로드
      </button>
    </div>
  );
}

