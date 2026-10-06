import { Icon, useToast } from '@/components/ui.jsx';
import { copyHandoffAddress } from '@/lib/handoffAddress.js';
import s from './AddressCopyChip.module.css';

export function AddressCopyChip() {
  const { push: pushToast } = useToast();
  return (
    <button type="button" className={s.chip} aria-label="주소 복사" onClick={() => copyHandoffAddress(pushToast)}>
      <span>{window.location.host}</span>
      <span className={s.icon} aria-hidden="true"><Icon name="copy" size={16} /></span>
    </button>
  );
}
