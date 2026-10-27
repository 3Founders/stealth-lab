-- Outreach contact list for Supabase (Postgres). Run once in the Supabase SQL editor of the outreach project.
-- Idempotent. Only the service-role key (backend/.env, never in a browser or a commit) can read or write these tables:
-- row-level security is on, there are NO policies, and anon/authenticated have no privileges.
-- Rules the database itself enforces (so a bug in a script cannot break them):
--   * nothing is ever deleted;
--   * a final status (unsubscribed, bounced, do_not_contact) never changes, except bounced -> unsubscribed.

create table if not exists outreach_contacts (
  email             text primary key check (email = lower(email)),
  name              text not null default '',
  company           text not null default '',
  country           text not null default '',
  source            text not null default '',
  basis             text not null default '',          -- why we contacted them (legitimate-interest note)
  status            text not null default 'new'
                    check (status in ('new','approved','sent','replied','unsubscribed','bounced','do_not_contact')),
  created_at        timestamptz,
  last_contacted_at timestamptz,
  final_at          timestamptz,
  final_reason      text
);

create table if not exists outreach_events (
  id     bigint generated always as identity primary key,
  at     timestamptz,
  email  text not null,
  kind   text not null,
  detail text not null default ''
);

create index if not exists outreach_contacts_status_idx on outreach_contacts (status);

create or replace function outreach_guard() returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'outreach rows are never deleted';
  end if;
  if tg_table_name = 'outreach_events' then
    raise exception 'outreach events are append-only';
  end if;
  if old.status in ('unsubscribed','bounced','do_not_contact') and new.status <> old.status
     and not (old.status = 'bounced' and new.status = 'unsubscribed') then
    raise exception 'a final status cannot be changed (% -> %)', old.status, new.status;
  end if;
  return new;
end $$;

drop trigger if exists outreach_contacts_guard on outreach_contacts;
create trigger outreach_contacts_guard before update or delete on outreach_contacts
  for each row execute function outreach_guard();

drop trigger if exists outreach_events_guard on outreach_events;
create trigger outreach_events_guard before update or delete on outreach_events
  for each row execute function outreach_guard();

alter table outreach_contacts enable row level security;
alter table outreach_events   enable row level security;
revoke all on outreach_contacts, outreach_events from anon, authenticated;
