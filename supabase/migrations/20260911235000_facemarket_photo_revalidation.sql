-- Photo edits retain identity while fencing work from earlier photo revisions.
alter table public.fm_biometric_enrollments
  add column photo_revision integer not null default 0
  check (photo_revision >= 0);
