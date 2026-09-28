create table if not exists medication_dose_events (
    medication_dose_event_id integer primary key,
    source_id integer not null references sources(source_id) on delete cascade,
    client_record_id text not null,
    medication_name text not null,
    medication_concept_key text,
    status text not null,
    status_raw integer not null,
    start_time text not null,
    scheduled_time text,
    dose real,
    unit text,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (source_id, client_record_id)
);

create index if not exists medication_dose_events_source_start_idx
    on medication_dose_events (source_id, start_time);

alter table sync_runs add column medication_dose_event_count integer not null default 0;
