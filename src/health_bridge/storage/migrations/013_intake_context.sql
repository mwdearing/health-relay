create table if not exists intake_producers (
    intake_producer_row_id integer primary key,
    owner_id text not null check (length(owner_id) > 0),
    producer_id text not null check (length(producer_id) > 0),
    writer_bundle_id text not null check (length(writer_bundle_id) > 0),
    display_label text not null,
    registered_at text not null,
    revoked_at text,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (owner_id, producer_id)
);

create table if not exists intake_state (
    owner_id text not null check (length(owner_id) > 0),
    producer_id text not null check (length(producer_id) > 0),
    intake_id text not null check (length(intake_id) > 0),
    current_revision integer not null
        check (
            typeof(current_revision) = 'integer'
            and current_revision between 1 and 9223372036854775807
        ),
    current_projection_sequence integer not null
        check (
            typeof(current_projection_sequence) = 'integer'
            and current_projection_sequence between 1 and 9223372036854775807
        ),
    deleted integer not null default 0 check (deleted in (0, 1)),
    updated_at text not null,
    primary key (owner_id, producer_id, intake_id)
);

create table if not exists intake_revisions (
    intake_revision_row_id integer primary key,
    owner_id text not null check (length(owner_id) > 0),
    producer_id text not null check (length(producer_id) > 0),
    intake_id text not null check (length(intake_id) > 0),
    revision integer not null
        check (
            typeof(revision) = 'integer'
            and revision between 1 and 9223372036854775807
        ),
    domain_facts_hash text not null
        check (
            length(domain_facts_hash) = 71
            and substr(domain_facts_hash, 1, 7) = 'sha256:'
            and substr(domain_facts_hash, 8) not glob '*[^0-9a-f]*'
        ),
    projection_hash text not null
        check (
            length(projection_hash) = 71
            and substr(projection_hash, 1, 7) = 'sha256:'
            and substr(projection_hash, 8) not glob '*[^0-9a-f]*'
        ),
    client_payload_hash text not null
        check (
            length(client_payload_hash) = 71
            and substr(client_payload_hash, 1, 7) = 'sha256:'
            and substr(client_payload_hash, 8) not glob '*[^0-9a-f]*'
        ),
    installation_id text not null,
    operation_id text not null check (length(operation_id) > 0),
    occurred_at text not null,
    time_zone text not null,
    recorded_at text not null,
    category text not null,
    display_name text not null check (length(display_name) > 0),
    serving_amount text not null
        check (
            length(serving_amount) > 0
            and serving_amount not glob '*[^0-9.]*'
            and serving_amount not glob '.*'
            and serving_amount not glob '*.'
            and serving_amount not glob '*.*.*'
            and serving_amount not glob '0[0-9]*'
        ),
    serving_unit text not null check (length(serving_unit) > 0),
    nutrition_completeness text not null
        check (nutrition_completeness in ('complete', 'partial', 'unknown')),
    received_at text not null,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (owner_id, producer_id, intake_id, revision)
);

create index if not exists idx_intake_revisions_producer_intake
    on intake_revisions (producer_id, intake_id);

create index if not exists idx_intake_revisions_revision
    on intake_revisions (revision);

create index if not exists idx_intake_revisions_occurred_at
    on intake_revisions (occurred_at);

create index if not exists idx_intake_revisions_operation_id
    on intake_revisions (operation_id);

create table if not exists intake_compound_facts (
    intake_fact_row_id integer primary key,
    intake_revision_row_id integer not null
        references intake_revisions (intake_revision_row_id),
    component_id text not null check (length(component_id) > 0),
    position integer not null check (typeof(position) = 'integer' and position >= 0),
    kind text not null check (kind in ('nutrient', 'compound', 'blend')),
    code text not null check (length(code) > 0),
    label_name text,
    value_state text not null
        check (value_state in ('known', 'unknown', 'not_applicable', 'below_reporting_threshold')),
    amount text
        check (
            amount is null
            or (
                length(amount) > 0
                and amount not glob '*[^0-9.]*'
                and amount not glob '.*'
                and amount not glob '*.'
                and amount not glob '*.*.*'
                and amount not glob '0[0-9]*'
            )
        ),
    unit text check (unit is null or length(unit) > 0),
    quantity_basis text
        check (
            quantity_basis is null
            or quantity_basis in ('compound_mass', 'active_nutrient_mass', 'unknown')
        ),
    aggregation_role text not null
        check (aggregation_role in ('context_only', 'compound_measurement', 'blend_total_only')),
    provenance text not null
        check (
            provenance in (
                'user_confirmed',
                'label_confirmed',
                'ocr_confirmed',
                'catalog_reference',
                'recipe_calculated',
                'estimated'
            )
        ),
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    check (
        (value_state = 'known' and amount is not null and unit is not null)
        or (value_state in ('unknown', 'not_applicable') and amount is null and unit is null)
        or (value_state = 'below_reporting_threshold' and amount is null)
    ),
    check (
        (kind = 'nutrient' and aggregation_role = 'context_only')
        or (kind = 'compound' and aggregation_role = 'compound_measurement' and quantity_basis is not null)
        or (kind = 'blend' and aggregation_role = 'blend_total_only')
    ),
    unique (intake_revision_row_id, component_id),
    unique (intake_revision_row_id, position)
);

create index if not exists idx_intake_compound_facts_component_id
    on intake_compound_facts (component_id);

create table if not exists intake_blend_members (
    intake_blend_member_row_id integer primary key,
    intake_fact_row_id integer not null
        references intake_compound_facts (intake_fact_row_id),
    position integer not null check (typeof(position) = 'integer' and position >= 0),
    label_name text not null check (length(label_name) > 0),
    amount text
        check (
            amount is null
            or (
                length(amount) > 0
                and amount not glob '*[^0-9.]*'
                and amount not glob '.*'
                and amount not glob '*.'
                and amount not glob '*.*.*'
                and amount not glob '0[0-9]*'
            )
        ),
    unit text check (unit is null or length(unit) > 0),
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    check ((amount is null) = (unit is null)),
    unique (intake_fact_row_id, position)
);

create table if not exists intake_projection_snapshots (
    intake_projection_row_id integer primary key,
    intake_revision_row_id integer not null
        references intake_revisions (intake_revision_row_id),
    projection_sequence integer not null
        check (
            typeof(projection_sequence) = 'integer'
            and projection_sequence between 1 and 9223372036854775807
        ),
    projection_hash text not null
        check (
            length(projection_hash) = 71
            and substr(projection_hash, 1, 7) = 'sha256:'
            and substr(projection_hash, 8) not glob '*[^0-9a-f]*'
        ),
    received_at text not null,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (intake_revision_row_id, projection_sequence)
);

create table if not exists intake_sample_links (
    intake_link_row_id integer primary key,
    intake_revision_row_id integer not null
        references intake_revisions (intake_revision_row_id),
    projection_sequence integer not null
        check (
            typeof(projection_sequence) = 'integer'
            and projection_sequence between 1 and 9223372036854775807
        ),
    component_id text not null check (length(component_id) > 0),
    healthkit_type text not null check (length(healthkit_type) > 0),
    sample_uuid text not null check (length(sample_uuid) > 0),
    sync_identifier text not null check (length(sync_identifier) > 0),
    sync_version integer not null
        check (
            typeof(sync_version) = 'integer'
            and sync_version between 1 and 9223372036854775807
        ),
    disposition text not null check (disposition in ('active', 'superseded', 'deleted')),
    source_bundle_id text,
    source_checked_at text,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    foreign key (intake_revision_row_id, projection_sequence)
        references intake_projection_snapshots (intake_revision_row_id, projection_sequence),
    foreign key (intake_revision_row_id, component_id)
        references intake_compound_facts (intake_revision_row_id, component_id),
    unique (intake_revision_row_id, projection_sequence, component_id, sample_uuid)
);

create index if not exists idx_intake_sample_links_sample_uuid
    on intake_sample_links (sample_uuid);

create index if not exists idx_intake_sample_links_component_id
    on intake_sample_links (component_id);

create table if not exists intake_operation_receipts (
    server_cursor integer primary key autoincrement,
    owner_id text not null check (length(owner_id) > 0),
    producer_id text not null check (length(producer_id) > 0),
    operation_id text not null check (length(operation_id) > 0),
    client_payload_hash text not null
        check (
            length(client_payload_hash) = 71
            and substr(client_payload_hash, 1, 7) = 'sha256:'
            and substr(client_payload_hash, 8) not glob '*[^0-9a-f]*'
        ),
    outcome text not null
        check (
            outcome in (
                'accepted',
                'duplicate',
                'stale_revision',
                'domain_conflict',
                'projection_conflict',
                'permanent_failure'
            )
        ),
    accepted_revision integer
        check (
            accepted_revision is null
            or (
                typeof(accepted_revision) = 'integer'
                and accepted_revision between 1 and 9223372036854775807
            )
        ),
    received_at text not null,
    result_json text check (result_json is null or json_valid(result_json)),
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (owner_id, producer_id, operation_id)
);

create table if not exists intake_tombstones (
    intake_tombstone_row_id integer primary key,
    owner_id text not null check (length(owner_id) > 0),
    producer_id text not null check (length(producer_id) > 0),
    intake_id text not null check (length(intake_id) > 0),
    deleted_at text not null,
    revision integer not null
        check (
            typeof(revision) = 'integer'
            and revision between 1 and 9223372036854775807
        ),
    operation_id text not null check (length(operation_id) > 0),
    domain_facts_hash text not null
        check (
            length(domain_facts_hash) = 71
            and substr(domain_facts_hash, 1, 7) = 'sha256:'
            and substr(domain_facts_hash, 8) not glob '*[^0-9a-f]*'
        ),
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unique (owner_id, producer_id, intake_id)
);

create trigger if not exists intake_revisions_are_immutable_on_change
before update on intake_revisions
begin
    select raise(abort, 'intake revisions are immutable');
end;

create trigger if not exists intake_revisions_are_permanent
before delete on intake_revisions
begin
    select raise(abort, 'intake revisions are permanent');
end;

create trigger if not exists intake_compound_facts_are_immutable_on_change
before update on intake_compound_facts
begin
    select raise(abort, 'intake facts are immutable');
end;

create trigger if not exists intake_compound_facts_are_permanent
before delete on intake_compound_facts
begin
    select raise(abort, 'intake facts are permanent');
end;

create trigger if not exists intake_blend_members_are_immutable_on_change
before update on intake_blend_members
begin
    select raise(abort, 'intake blend members are immutable');
end;

create trigger if not exists intake_blend_members_are_permanent
before delete on intake_blend_members
begin
    select raise(abort, 'intake blend members are permanent');
end;

create trigger if not exists intake_projection_snapshots_are_immutable_on_change
before update on intake_projection_snapshots
begin
    select raise(abort, 'intake projection snapshots are immutable');
end;

create trigger if not exists intake_projection_snapshots_are_permanent
before delete on intake_projection_snapshots
begin
    select raise(abort, 'intake projection snapshots are permanent');
end;

create trigger if not exists intake_sample_links_keep_their_claim
before update of
    intake_revision_row_id,
    projection_sequence,
    component_id,
    healthkit_type,
    sample_uuid,
    sync_identifier,
    sync_version,
    disposition,
    created_at
on intake_sample_links
begin
    select raise(abort, 'intake sample links keep the claim they were stored with');
end;

create trigger if not exists intake_sample_links_are_retained_for_audit
before delete on intake_sample_links
begin
    select raise(abort, 'intake sample links are retained for audit');
end;

create trigger if not exists intake_operation_receipts_are_immutable_on_change
before update on intake_operation_receipts
begin
    select raise(abort, 'intake operation receipts are immutable');
end;

create trigger if not exists intake_operation_receipts_are_permanent
before delete on intake_operation_receipts
begin
    select raise(abort, 'intake operation receipts are permanent');
end;

create trigger if not exists intake_tombstones_are_immutable_on_change
before update on intake_tombstones
begin
    select raise(abort, 'intake tombstones are permanent');
end;

create trigger if not exists intake_tombstones_are_permanent
before delete on intake_tombstones
begin
    select raise(abort, 'intake tombstones are permanent');
end;
