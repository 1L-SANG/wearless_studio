-- A refund that has started an external Toss cancellation must not be rejectable.
-- The same row stays retryable with the refund request UUID as Toss Idempotency-Key.
alter table public.refund_requests
  drop constraint if exists refund_requests_status_check;

alter table public.refund_requests
  add constraint refund_requests_status_check
  check (status in ('pending', 'canceling', 'approved', 'rejected'));

alter table public.refund_requests
  drop constraint if exists refund_requests_check;

alter table public.refund_requests
  add constraint refund_requests_resolved_check
  check (status in ('pending', 'canceling') or resolved_at is not null);

drop index if exists public.refund_requests_pending_unique_idx;
create unique index refund_requests_open_unique_idx
  on public.refund_requests (credit_source_id)
  where status in ('pending', 'canceling');
