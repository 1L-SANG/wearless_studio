import { ImagePlus, Trash2, Upload, X } from 'lucide-react';
import { useImageUpload } from '@/components/hooks/use-image-upload.js';
import s from './ImageUpload.module.css';

export function ImageUpload({
  previewUrl,
  fileName,
  onSelect,
  onRemove,
  onReject,
  accept = 'image/png,image/jpeg,image/webp',
  disabled = false,
  uploading = false,
  title = '클릭해서 사진 선택',
  description = '또는 이곳에 파일을 끌어 놓으세요',
  formatHint = 'JPG, PNG, WEBP',
  busyLabel = '사진을 처리하고 있어요',
  alt = '선택한 이미지 미리보기',
  className = '',
}) {
  const upload = useImageUpload({ accept, disabled: disabled || uploading, onSelect, onReject });
  const dropProps = {
    onDragOver: upload.handleDragOver,
    onDragEnter: upload.handleDragEnter,
    onDragLeave: upload.handleDragLeave,
    onDrop: upload.handleDrop,
  };

  return (
    <div className={`${s.root}${className ? ` ${className}` : ''}`} aria-busy={uploading}>
      <input
        ref={upload.fileInputRef}
        className={s.fileInput}
        type="file"
        hidden
        accept={accept}
        tabIndex={-1}
        aria-hidden="true"
        disabled={disabled || uploading}
        onChange={upload.handleFileChange}
      />

      {!previewUrl ? (
        <button
          type="button"
          className={`${s.dropzone} ${upload.isDragging ? s.dragging : ''}`}
          disabled={disabled || uploading}
          data-image-dropzone
          onClick={upload.chooseFile}
          {...dropProps}
        >
          <span className={s.iconCircle}><ImagePlus size={25} aria-hidden="true" /></span>
          <span className={s.prompt}>{uploading ? busyLabel : title}</span>
          <span className={s.description}>{description}</span>
          <span className={s.format}>{formatHint}</span>
        </button>
      ) : (
        <div className={`${s.selected} ${upload.isDragging ? s.draggingPreview : ''}`} {...dropProps}>
          <div className={s.preview}>
            <img src={previewUrl} alt={alt} />
            <span className={s.shade} aria-hidden="true" />
            <div className={s.actions}>
              <button type="button" className={s.iconButton} aria-label="다른 사진 선택" disabled={disabled || uploading} onClick={upload.chooseFile}>
                <Upload size={17} aria-hidden="true" />
              </button>
              <button type="button" className={`${s.iconButton} ${s.deleteButton}`} aria-label="사진 삭제" disabled={disabled || uploading} onClick={onRemove}>
                <Trash2 size={17} aria-hidden="true" />
              </button>
            </div>
            {uploading && <span className={s.uploading} role="status">{busyLabel}</span>}
          </div>
          <div className={s.fileRow}>
            <span title={fileName || undefined}>{fileName || '선택한 사진'}</span>
            <button type="button" aria-label="선택한 사진 지우기" disabled={disabled || uploading} onClick={onRemove}>
              <X size={17} aria-hidden="true" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default ImageUpload;
