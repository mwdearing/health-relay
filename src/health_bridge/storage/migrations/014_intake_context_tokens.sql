create table if not exists intake_context_tokens (
    intake_token_id integer primary key,
    owner_id text not null,
    producer_id text not null,
    label text not null,
    token_hash text not null unique,
    token_prefix text not null,
    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    revoked_at text
);

create index if not exists idx_intake_context_tokens_prefix_active
    on intake_context_tokens(token_prefix)
    where revoked_at is null;
