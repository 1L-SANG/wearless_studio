import { useCallback, useRef, useState } from 'react';

function acceptsFile(file, accept) {
  const rules = String(accept || 'image/*').split(',').map((rule) => rule.trim().toLowerCase()).filter(Boolean);
  const type = String(file?.type || '').toLowerCase();
  const name = String(file?.name || '').toLowerCase();
  const knownExtensions = {
    'image/jpeg': ['.jpg', '.jpeg'],
    'image/png': ['.png'],
    'image/webp': ['.webp'],
    'image/gif': ['.gif'],
  };
  return rules.some((rule) => {
    if (rule.endsWith('/*')) {
      if (type.startsWith(rule.slice(0, -1))) return true;
      return !type && rule === 'image/*' && /\.(avif|gif|heic|heif|jpe?g|png|svg|webp)$/.test(name);
    }
    if (rule.startsWith('.')) return name.endsWith(rule);
    return type === rule || (!type && knownExtensions[rule]?.some((extension) => name.endsWith(extension)));
  });
}

export function useImageUpload({ accept = 'image/*', disabled = false, onSelect, onReject } = {}) {
  const fileInputRef = useRef(null);
  const [isDragging, setIsDragging] = useState(false);

  const chooseFile = useCallback(() => {
    if (!disabled) fileInputRef.current?.click();
  }, [disabled]);

  const selectFile = useCallback((file) => {
    if (!file || disabled) return;
    if (!acceptsFile(file, accept)) {
      onReject?.(file);
      return;
    }
    onSelect?.(file);
  }, [accept, disabled, onReject, onSelect]);

  const handleFileChange = useCallback((event) => {
    selectFile(event.target.files?.[0]);
    event.target.value = '';
  }, [selectFile]);

  const handleDragOver = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
  }, []);

  const handleDragEnter = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!disabled) setIsDragging(true);
  }, [disabled]);

  const handleDragLeave = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!event.currentTarget?.contains?.(event.relatedTarget)) setIsDragging(false);
  }, []);

  const handleDrop = useCallback((event) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
    selectFile(event.dataTransfer.files?.[0]);
  }, [selectFile]);

  return {
    fileInputRef,
    isDragging,
    chooseFile,
    handleFileChange,
    handleDragOver,
    handleDragEnter,
    handleDragLeave,
    handleDrop,
  };
}
