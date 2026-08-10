import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Modal } from '@/components/ui.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { recordCreditReturn } from '@/lib/creditReturn.js';

export function CreditShortfallModal({ shortfall, action, onClose }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  if (!shortfall) return null;

  const goToPricing = () => {
    recordCreditReturn({
      projectId: useAppStore.getState().projectId,
      path: pathname,
      action,
      requiredCredits: shortfall.requiredCredits,
    });
    onClose();
    navigate('/pricing');
  };

  return (
    <Modal onClose={onClose}>
      <p>{shortfall.message}</p>
      <div className="modal-actions">
        <Button variant="primary" onClick={goToPricing}>충전하러 가기</Button>
        <Button variant="ghost" onClick={onClose}>닫기</Button>
      </div>
    </Modal>
  );
}
