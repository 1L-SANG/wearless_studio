alter table public.fm_model_test_cuts add column if not exists kind text;
update public.fm_model_test_cuts set kind = 'closeup' where kind is null;
alter table public.fm_model_test_cuts alter column kind set not null;
alter table public.fm_model_test_cuts drop constraint if exists fm_model_test_cuts_kind_check;
alter table public.fm_model_test_cuts add constraint fm_model_test_cuts_kind_check
  check (kind in ('closeup', 'fullbody'));

alter table public.fm_models add column if not exists fullbody_image_url text;
