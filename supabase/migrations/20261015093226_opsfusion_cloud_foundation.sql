-- Single-site OpsFusion cloud foundation. Membership is assigned out of band;
-- authenticating alone never grants access to recordings or reports.
create table public.opsfusion_members (
  user_id uuid primary key references auth.users(id) deferrable initially immediate,
  role text not null check (role in ('admin','operator','viewer')),
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);
alter table public.opsfusion_members enable row level security;
revoke all on public.opsfusion_members from anon, authenticated;
grant select on public.opsfusion_members to authenticated;
grant all on public.opsfusion_members to service_role;
create policy member_reads_self on public.opsfusion_members for select to authenticated
  using (user_id = (select auth.uid()) and enabled);

create function public.opsfusion_role() returns text
language sql stable security invoker set search_path = '' as $$
  select role from public.opsfusion_members
  where user_id = (select auth.uid()) and enabled;
$$;
revoke all on function public.opsfusion_role() from public, anon;
grant execute on function public.opsfusion_role() to authenticated, service_role;

create table public.opsfusion_jobs (
  id uuid primary key,
  created_by uuid not null default auth.uid() references auth.users(id) deferrable initially immediate,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  asset_id text not null check (length(btrim(asset_id)) between 1 and 256),
  camera_id text not null default 'camera-01' check (length(btrim(camera_id)) between 1 and 256),
  status text not null default 'draft' check (status in ('draft','queued','running','succeeded','failed')),
  input jsonb not null check (jsonb_typeof(input) = 'object' and octet_length(input::text) <= 32768),
  progress text not null default '' check (length(progress) <= 300),
  attempts integer not null default 0 check (attempts between 0 and 3),
  worker_id uuid,
  lease_token uuid,
  lease_until timestamptz,
  result jsonb check (result is null or (jsonb_typeof(result) = 'object' and octet_length(result::text) <= 2097152)),
  error text check (error is null or length(error) <= 1000)
);
create index opsfusion_jobs_created on public.opsfusion_jobs (created_at desc);
create index opsfusion_jobs_actor on public.opsfusion_jobs (created_by);
create index opsfusion_jobs_queue on public.opsfusion_jobs (status, created_at) where status in ('queued','running');
alter table public.opsfusion_jobs enable row level security;
revoke all on public.opsfusion_jobs from anon, authenticated;
grant select on public.opsfusion_jobs to authenticated;
grant insert (id, asset_id, camera_id, input) on public.opsfusion_jobs to authenticated;
grant update (status) on public.opsfusion_jobs to authenticated;
grant all on public.opsfusion_jobs to service_role;
create policy members_read_jobs on public.opsfusion_jobs for select to authenticated
  using ((select public.opsfusion_role()) is not null);
create policy operators_create_draft on public.opsfusion_jobs for insert to authenticated
  with check (created_by = (select auth.uid()) and status = 'draft' and attempts = 0
    and (select public.opsfusion_role()) in ('admin','operator'));
create policy owner_submits_draft on public.opsfusion_jobs for update to authenticated
  using (created_by = (select auth.uid()) and status = 'draft'
    and (select public.opsfusion_role()) in ('admin','operator'))
  with check (created_by = (select auth.uid()) and status = 'queued'
    and (select public.opsfusion_role()) in ('admin','operator'));

create table public.opsfusion_incidents (
  job_id uuid not null references public.opsfusion_jobs(id),
  incident_id text not null check (length(incident_id) between 1 and 256),
  first_report jsonb not null check (jsonb_typeof(first_report) = 'object'),
  current_report jsonb not null check (jsonb_typeof(current_report) = 'object'),
  created_at timestamptz not null default now(),
  primary key (job_id, incident_id)
);
alter table public.opsfusion_incidents enable row level security;
revoke all on public.opsfusion_incidents from anon, authenticated;
grant select on public.opsfusion_incidents to authenticated;
grant all on public.opsfusion_incidents to service_role;
create policy members_read_incidents on public.opsfusion_incidents for select to authenticated
  using ((select public.opsfusion_role()) is not null);

create table public.opsfusion_reviews (
  id bigint generated always as identity primary key,
  job_id uuid not null,
  incident_id text not null,
  actor uuid not null default auth.uid() references auth.users(id) deferrable initially immediate,
  status text not null check (status in ('new','acknowledged','resolved','false_alarm')),
  note text not null default '' check (length(note) <= 2000),
  created_at timestamptz not null default now(),
  foreign key (job_id, incident_id) references public.opsfusion_incidents(job_id, incident_id)
);
create index opsfusion_reviews_incident on public.opsfusion_reviews (job_id, incident_id, id desc);
create index opsfusion_reviews_actor on public.opsfusion_reviews (actor);
alter table public.opsfusion_reviews enable row level security;
revoke all on public.opsfusion_reviews from anon, authenticated;
grant select on public.opsfusion_reviews to authenticated;
grant insert (job_id, incident_id, status, note) on public.opsfusion_reviews to authenticated;
grant usage on sequence public.opsfusion_reviews_id_seq to authenticated, service_role;
grant all on public.opsfusion_reviews to service_role;
create policy members_read_reviews on public.opsfusion_reviews for select to authenticated
  using ((select public.opsfusion_role()) is not null);
create policy operators_append_reviews on public.opsfusion_reviews for insert to authenticated
  with check (actor = (select auth.uid()) and (select public.opsfusion_role()) in ('admin','operator'));

create table public.opsfusion_workers (
  id uuid primary key,
  label text not null check (length(label) between 1 and 64),
  last_seen timestamptz not null default now()
);
alter table public.opsfusion_workers enable row level security;
revoke all on public.opsfusion_workers from anon, authenticated;
grant select on public.opsfusion_workers to authenticated;
grant all on public.opsfusion_workers to service_role;
create policy members_read_workers on public.opsfusion_workers for select to authenticated
  using ((select public.opsfusion_role()) is not null);

create function public.opsfusion_worker_ping(p_worker_id uuid, p_label text) returns void
language sql security invoker set search_path = '' as $$
  insert into public.opsfusion_workers(id, label, last_seen)
  values (p_worker_id, left(p_label,64), clock_timestamp())
  on conflict (id) do update set label=excluded.label, last_seen=excluded.last_seen;
$$;

create function public.opsfusion_claim_job(p_worker_id uuid) returns setof public.opsfusion_jobs
language plpgsql security invoker set search_path = '' as $$
begin
  update public.opsfusion_jobs set status='failed', error='Worker lease expired after three attempts',
    updated_at=clock_timestamp(), lease_token=null, lease_until=null
  where status='running' and lease_until < clock_timestamp() and attempts >= 3;
  return query
  update public.opsfusion_jobs as j set status='running', attempts=j.attempts+1,
    worker_id=p_worker_id, lease_token=gen_random_uuid(),
    lease_until=clock_timestamp()+interval '120 seconds', updated_at=clock_timestamp(),
    progress='Processing uploaded recordings', error=null
  where j.id = (
    select q.id from public.opsfusion_jobs q
    where (q.status='queued' or (q.status='running' and q.lease_until < clock_timestamp())) and q.attempts < 3
    order by q.created_at limit 1 for update skip locked
  ) returning j.*;
end;
$$;

create function public.opsfusion_heartbeat(p_job_id uuid, p_lease_token uuid, p_progress text) returns boolean
language plpgsql security invoker set search_path = '' as $$
begin
  update public.opsfusion_jobs set lease_until=clock_timestamp()+interval '120 seconds',
    updated_at=clock_timestamp(), progress=left(p_progress,300)
  where id=p_job_id and status='running' and lease_token=p_lease_token and lease_until>clock_timestamp();
  return found;
end;
$$;

create function public.opsfusion_finish_job(p_job_id uuid, p_lease_token uuid, p_result jsonb, p_incidents jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare item jsonb;
begin
  perform 1 from public.opsfusion_jobs where id=p_job_id and status='running'
    and lease_token=p_lease_token and lease_until>clock_timestamp() for update;
  if not found then return false; end if;
  if p_result is null or jsonb_typeof(p_result) <> 'object' or p_incidents is null
    or jsonb_typeof(p_incidents) <> 'array' or jsonb_array_length(p_incidents)>200
    or octet_length(p_incidents::text)>2097152 then raise exception 'Invalid incident result'; end if;
  for item in select value from jsonb_array_elements(p_incidents) loop
    insert into public.opsfusion_incidents(job_id, incident_id, first_report, current_report)
    values (p_job_id, item->>'incident_id', item->'first_report', item->'current_report');
  end loop;
  update public.opsfusion_jobs set status='succeeded', result=p_result, progress='Analysis complete',
    updated_at=clock_timestamp(), lease_token=null, lease_until=null where id=p_job_id;
  return true;
end;
$$;

create function public.opsfusion_fail_job(p_job_id uuid, p_lease_token uuid, p_error text) returns boolean
language plpgsql security invoker set search_path = '' as $$
begin
  update public.opsfusion_jobs set status='failed', error=left(p_error,1000),
    updated_at=clock_timestamp(), lease_token=null, lease_until=null
  where id=p_job_id and status='running' and lease_token=p_lease_token and lease_until>clock_timestamp();
  return found;
end;
$$;

revoke all on function public.opsfusion_worker_ping(uuid,text), public.opsfusion_claim_job(uuid),
  public.opsfusion_heartbeat(uuid,uuid,text), public.opsfusion_finish_job(uuid,uuid,jsonb,jsonb),
  public.opsfusion_fail_job(uuid,uuid,text) from public, anon, authenticated;
grant execute on function public.opsfusion_worker_ping(uuid,text), public.opsfusion_claim_job(uuid),
  public.opsfusion_heartbeat(uuid,uuid,text), public.opsfusion_finish_job(uuid,uuid,jsonb,jsonb),
  public.opsfusion_fail_job(uuid,uuid,text) to service_role;

insert into storage.buckets (id, name, public, file_size_limit)
values ('opsfusion-inputs','opsfusion-inputs',false,52428800),
       ('opsfusion-results','opsfusion-results',false,52428800);

create policy opsfusion_private_read on storage.objects for select to authenticated
  using (bucket_id in ('opsfusion-inputs','opsfusion-results') and (select public.opsfusion_role()) is not null);
create policy opsfusion_owner_upload on storage.objects for insert to authenticated with check (
  bucket_id='opsfusion-inputs' and (select public.opsfusion_role()) in ('admin','operator')
  and (storage.foldername(name))[1] = (select auth.uid())::text
  and array_length(storage.foldername(name),1)=2
  and storage.filename(name) ~ '^(video|sensor|sensor_train|sensor_validation|audio|audio_train|audio_validation|logs)\.(mp4|avi|mov|mkv|csv|wav)$'
  and exists (select 1 from public.opsfusion_jobs j where j.id::text=(storage.foldername(name))[2]
    and j.created_by=(select auth.uid()) and j.status='draft')
);
-- No client UPDATE or DELETE policies: inputs cannot be replaced after submission,
-- and clients cannot forge worker results or erase audit/report history.
