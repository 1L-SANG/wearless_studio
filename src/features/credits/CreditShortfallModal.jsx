import { useNavigate } from 'react-router-dom';
import { Button, Modal } from '@/components/ui.jsx';

export function CreditShortfallModal({ shortfall, onClose }) {
  const navigate = useNavigate();
  if (!shortfall) return null;

  const goToPricing = () => {
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
