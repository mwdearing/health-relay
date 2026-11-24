-- Ported from upstream 011_daily_projection_fence_order (receiver-v1.2.0), renumbered to 018_daily_projection_fence_order because this fork's
-- 010 to 016 are already taken (FORK.md, docs/reference/sqlite-v1.md). SQL unchanged.

create index daily_projection_fence_order on daily_projection_fences(source_id, type_code, namespace, sequence);
