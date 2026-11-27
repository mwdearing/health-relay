-- Ported from upstream 012_catalogue_coverage_index (receiver-v1.2.0), renumbered to 019_catalogue_coverage_index because this fork's
-- 010 to 016 are already taken (FORK.md, docs/reference/sqlite-v1.md). SQL unchanged.

create index samples_type_coverage
    on samples(type_code, start_time, sample_id, end_time);
