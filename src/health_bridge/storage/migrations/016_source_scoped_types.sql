-- Key the type catalog by (source_id, type_code) (RELAY-S15, #135).
-- health_types was one global registry, so the first device to declare a type
-- decided its metadata for every other device. Each source now owns its own
-- rows. SQLite cannot change a primary key or add a composite foreign key in
-- place, so health_types, health_type_aliases and samples are rebuilt; foreign
-- key checks are deferred to commit so the parent can be swapped.
-- Existing catalog rows are kept for the sources that stored samples of the type
-- (plus the lowest source for an unsampled type); later declarations only ever
-- touch the declaring source's own rows, and a source that reports a known type
-- inherits its existing metadata at write time.

pragma defer_foreign_keys = on;

alter table health_types rename to health_types_old;

create table health_types (
    source_id integer not null references sources(source_id) on delete cascade,
    type_code text not null,
    display_name text not null,
    category text not null,
    default_unit text not null,
    sensitivity text not null,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    primary key (source_id, type_code)
);

insert into health_types (
    source_id, type_code, display_name, category, default_unit, sensitivity,
    created_at, updated_at
)
select o.source_id, t.type_code, t.display_name, t.category, t.default_unit,
       t.sensitivity, t.created_at, t.updated_at
from health_types_old as t
join (
    -- Only real ownership pairs: sources that stored samples of the type, and
    -- the lowest source for a type nothing has sampled yet. Never sources x types.
    select distinct source_id, type_code from samples
    union
    select (select min(source_id) from sources), type_code
    from health_types_old
    where type_code not in (select type_code from samples)
      and exists (select 1 from sources)
) as o on o.type_code = t.type_code
order by t.created_at, t.rowid;

create table health_type_aliases_v2 (
    alias_id integer primary key,
    source_id integer not null,
    type_code text not null,
    alias text not null,
    unique (source_id, type_code, alias),
    foreign key (source_id, type_code)
        references health_types(source_id, type_code) on delete cascade
);

insert into health_type_aliases_v2 (source_id, type_code, alias)
select t.source_id, a.type_code, a.alias
from (
    -- Legacy databases may hold more aliases per type than the runtime cap of
    -- 64; rank once per type (single pass) and carry the oldest 64.
    select type_code, alias,
           row_number() over (partition by type_code order by alias_id) as rank_in_type
    from health_type_aliases
) as a
join health_types as t on t.type_code = a.type_code
where a.rank_in_type <= 64;

create table samples_v2 (
    sample_id integer primary key,
    source_id integer not null references sources(source_id) on delete cascade,
    type_code text not null,
    client_record_id text not null,
    start_time text not null,
    end_time text not null,
    value real not null,
    unit text not null,
    metadata_json text not null,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (source_id, type_code, client_record_id),
    foreign key (source_id, type_code)
        references health_types(source_id, type_code) on delete restrict
);

insert into samples_v2 (
    sample_id, source_id, type_code, client_record_id, start_time, end_time,
    value, unit, metadata_json, created_at, updated_at
)
select sample_id, source_id, type_code, client_record_id, start_time, end_time,
       value, unit, metadata_json, created_at, updated_at
from samples;

drop table samples;
drop table health_type_aliases;
drop table health_types_old;
alter table health_type_aliases_v2 rename to health_type_aliases;
alter table samples_v2 rename to samples;

create trigger if not exists samples_interval_order_insert
before insert on samples
when new.end_time < new.start_time
begin
    select raise(abort, 'sample interval reversed');
end;

create trigger if not exists samples_interval_order_update
before update on samples
when new.end_time < new.start_time
begin
    select raise(abort, 'sample interval reversed');
end;

-- One row per type for readers that report the catalog, from the earliest
-- declaration (lowest rowid, independent of source registration order); the
-- owning rows stay per source above.
create index if not exists health_types_type_code_idx
    on health_types (type_code, source_id);

create view if not exists health_type_catalog as
select type_code, display_name, category, default_unit, sensitivity
from health_types
where rowid = (
    select min(inner_type.rowid)
    from health_types as inner_type
    where inner_type.type_code = health_types.type_code
);
