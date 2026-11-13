import React from 'react';
import { createPortal } from 'react-dom';
import styles from './StoryboardLayout.module.css';

export function StoryboardBackdrop() {
  const backdrop = <div className={`sb-v5-theme ${styles.backdrop}`} aria-hidden="true"><i className={styles.orbOne} /><i className={styles.orbTwo} /></div>;
  return typeof document !== 'undefined' && document.body ? createPortal(backdrop, document.body) : backdrop;
}
