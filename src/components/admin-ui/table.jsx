import { cn } from '@/lib/adminCn.js';

export function Table({ className, ...props }) {
  // 표는 자기 컨테이너 안에서 가로 스크롤한다 — 페이지 몸통이 옆으로 밀리지 않게.
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  );
}
export function TableHeader({ className, ...props }) {
  return <thead className={cn('[&_tr]:border-b [&_tr]:border-border', className)} {...props} />;
}
export function TableBody({ className, ...props }) {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />;
}
export function TableRow({ className, ...props }) {
  return <tr className={cn('border-b border-border transition-colors hover:bg-muted/50', className)} {...props} />;
}
export function TableHead({ className, ...props }) {
  return <th className={cn('h-10 px-3 text-left align-middle text-xs font-medium text-muted-foreground', className)} {...props} />;
}
export function TableCell({ className, ...props }) {
  return <td className={cn('px-3 py-2.5 align-middle', className)} {...props} />;
}
