create table if not exists electrocardiograms (
    electrocardiogram_id integer primary key,
    source_id integer not null references sources(source_id) on delete cascade,
    client_record_id text not null,
    start_time text not null,
    end_time text not null,
    classification text not null,
    symptoms_status text not null,
    average_heart_rate_bpm real,
    sampling_frequency_hz real,
    voltage_count integer not null default 0,
    voltages_json text,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (source_id, client_record_id)
);

create index if not exists electrocardiograms_source_start_idx
    on electrocardiograms (source_id, start_time);

alter table sync_runs add column electrocardiogram_count integer not null default 0;
