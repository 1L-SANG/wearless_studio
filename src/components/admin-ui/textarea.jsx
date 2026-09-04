import { cn } from '@/lib/adminCn.js';

// input.jsx 와 같은 테두리·포커스링·비활성 처리를 쓴다 — 관리자 화면 안에서 입력 위젯의
// 시각 언어가 갈라지지 않게. 거절 사유처럼 여러 줄이 필요한 프로즈(지원자에게 메일로
// 전달되는 문장)를 <Input> 한 줄에 욱여넣지 않으려고 따로 둔다.
export function Textarea({ className, rows = 3, ...props }) {
  return (
    <textarea
      rows={rows}
      className={cn(
        'flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm',
        'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
