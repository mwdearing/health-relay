create table if not exists lab_results (
    lab_result_id integer primary key,
    source_id integer not null references sources(source_id) on delete cascade,
    client_record_id text not null,
    loinc text,
    name text not null,
    category text,
    effective_date text not null,
    value_num real,
    unit text,
    value_text text,
    ref_low real,
    ref_high real,
    ref_text text,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (source_id, client_record_id)
);

create index if not exists lab_results_source_effective_date_idx
    on lab_results (source_id, effective_date);

alter table sync_runs add column lab_result_count integer not null default 0;
