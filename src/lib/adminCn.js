/* shadcn 컴포넌트가 공유하는 클래스 병합기. tailwind-merge 가 뒤에 온 유틸리티를
   이기게 해 준다(예: 기본 px-4 를 호출부의 px-2 로 덮기). admin 번들 전용이다. */
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
