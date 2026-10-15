-- Runs against the actual schema, using transaction-local synthetic identities.
-- All fixtures and role/claim changes are rolled back; no auth user is created.
begin;
set constraints all deferred;
do $$
declare
  admin_id uuid := gen_random_uuid();
  viewer_id uuid := gen_random_uuid();
  operator_id uuid := gen_random_uuid();
  outsider_id uuid := gen_random_uuid();
  job_id uuid := gen_random_uuid();
  draft_id uuid := gen_random_uuid();
  worker uuid := gen_random_uuid();
  claimed public.opsfusion_jobs%rowtype;
  blocked boolean;
  count_rows integer;
begin
  if exists(select 1 from public.opsfusion_jobs where status in ('queued','running')) then
    raise exception 'Run access fixtures only when no cloud jobs are active';
  end if;
  insert into public.opsfusion_members(user_id,role) values
    (admin_id,'admin'),(viewer_id,'viewer'),(operator_id,'operator');

  perform set_config('request.jwt.claims', json_build_object('sub',admin_id,'role','authenticated')::text, true);
  set local role authenticated;
  if public.opsfusion_role() <> 'admin' then raise exception 'Admin role lookup failed'; end if;
  insert into public.opsfusion_jobs(id,asset_id,input) values (job_id,'access-fixture','{}');
  insert into public.opsfusion_jobs(id,asset_id,input) values (draft_id,'access-fixture','{}');

  blocked := false;
  begin
    update public.opsfusion_members set role='viewer' where user_id=admin_id;
  exception when insufficient_privilege then blocked := true;
  end;
  if not blocked then raise exception 'Client can change membership'; end if;

  blocked := false;
  begin
    update public.opsfusion_jobs set input='{"forged":true}' where id=job_id;
  exception when insufficient_privilege then blocked := true;
  end;
  if not blocked then raise exception 'Client can change submitted input'; end if;

  insert into storage.objects(bucket_id,name) values
    ('opsfusion-inputs', admin_id::text||'/'||draft_id::text||'/logs.csv');
  update storage.objects set name=admin_id::text||'/'||draft_id::text||'/sensor.csv'
    where bucket_id='opsfusion-inputs' and name=admin_id::text||'/'||draft_id::text||'/logs.csv';
  get diagnostics count_rows = row_count;
  if count_rows <> 0 then raise exception 'Client can replace immutable input'; end if;

  update public.opsfusion_jobs set status='queued' where id=job_id;
  blocked := false;
  begin
    insert into storage.objects(bucket_id,name) values
      ('opsfusion-inputs', admin_id::text||'/'||job_id::text||'/logs.csv');
  exception when insufficient_privilege then blocked := true;
  end;
  if not blocked then raise exception 'Client can upload after submission'; end if;
  if has_function_privilege('authenticated','public.opsfusion_claim_job(uuid)','EXECUTE') then
    raise exception 'Client can invoke worker RPC';
  end if;
  reset role;

  set local role service_role;
  select * into claimed from public.opsfusion_claim_job(worker);
  if claimed.id is distinct from job_id then raise exception 'Worker did not claim expected job'; end if;
  if public.opsfusion_heartbeat(job_id,gen_random_uuid(),'forged heartbeat') then
    raise exception 'Wrong lease was accepted';
  end if;
  if not public.opsfusion_heartbeat(job_id,claimed.lease_token,'fixture heartbeat') then
    raise exception 'Valid lease was rejected';
  end if;
  if not public.opsfusion_finish_job(job_id,claimed.lease_token,'{}',
    '[{"incident_id":"access-fixture","first_report":{"synthetic":true},"current_report":{"synthetic":true}}]') then
    raise exception 'Valid result commit failed';
  end if;
  if public.opsfusion_finish_job(job_id,claimed.lease_token,'{}','[]') then
    raise exception 'A completed job can be overwritten';
  end if;
  update public.opsfusion_jobs set status='queued' where id=draft_id;
  select * into claimed from public.opsfusion_claim_job(worker);
  update public.opsfusion_jobs set lease_until=now()-interval '1 second' where id=draft_id;
  if public.opsfusion_heartbeat(draft_id,claimed.lease_token,'expired')
    or public.opsfusion_finish_job(draft_id,claimed.lease_token,'{}','[]') then
    raise exception 'Expired worker can extend or finish a lease';
  end if;
  perform 1 from public.opsfusion_claim_job(gen_random_uuid());
  if public.opsfusion_finish_job(draft_id,claimed.lease_token,'{}','[]') then
    raise exception 'Reclaimed job accepts the stale worker';
  end if;
  select * into claimed from public.opsfusion_jobs where id=job_id;
  reset role;

  perform set_config('request.jwt.claims', json_build_object('sub',viewer_id,'role','authenticated')::text, true);
  set local role authenticated;
  if (select count(*) from public.opsfusion_incidents i where i.job_id=claimed.id) < 1 then
    raise exception 'Viewer cannot read site incidents';
  end if;
  blocked := false;
  begin
    insert into public.opsfusion_jobs(id,asset_id,input) values (gen_random_uuid(),'viewer-forgery','{}');
  exception when insufficient_privilege then blocked := true;
  end;
  if not blocked then raise exception 'Viewer can submit jobs'; end if;
  blocked := false;
  begin
    insert into public.opsfusion_reviews(job_id,incident_id,status) values (job_id,'access-fixture','resolved');
  exception when insufficient_privilege then blocked := true;
  end;
  if not blocked then raise exception 'Viewer can review'; end if;
  reset role;

  perform set_config('request.jwt.claims', json_build_object('sub',operator_id,'role','authenticated')::text, true);
  set local role authenticated;
  insert into public.opsfusion_reviews(job_id,incident_id,status,note)
    values(job_id,'access-fixture','acknowledged','Transaction-local fixture');
  if not exists(select 1 from public.opsfusion_reviews r where r.actor=operator_id) then
    raise exception 'Review actor not preserved';
  end if;
  if has_table_privilege('authenticated','public.opsfusion_incidents','UPDATE') then
    raise exception 'Report snapshots are mutable by clients';
  end if;
  reset role;

  perform set_config('request.jwt.claims', json_build_object('sub',outsider_id,'role','authenticated')::text, true);
  set local role authenticated;
  if exists(select 1 from public.opsfusion_jobs) or exists(select 1 from public.opsfusion_incidents)
    or exists(select 1 from public.opsfusion_reviews) then raise exception 'Nonmember can read site data'; end if;
  if exists(select 1 from storage.objects where bucket_id in ('opsfusion-inputs','opsfusion-results')) then
    raise exception 'Nonmember can read private objects';
  end if;
  reset role;

  set local role anon;
  if has_table_privilege('anon','public.opsfusion_jobs','SELECT') then
    raise exception 'Anonymous job access is granted';
  end if;
  if exists(select 1 from storage.objects where bucket_id in ('opsfusion-inputs','opsfusion-results')) then
    raise exception 'Anonymous storage access is granted';
  end if;
  reset role;
end;
$$;
rollback;
select 'OpsFusion role, job, lease, review and storage checks passed; fixtures rolled back' as result;
