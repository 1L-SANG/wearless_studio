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
    // 추가 구매 탭으로 바로 열고 부족분을 넘긴다 — 요금제 화면이 그걸 채우는 팩에 '추천'을 붙인다.
    const need = Math.max(0, Number(shortfall.requiredCredits) - Number(shortfall.availableCredits));
    navigate(Number.isFinite(need) && need > 0 ? `/pricing?tab=topup&need=${need}` : '/pricing?tab=topup');
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
