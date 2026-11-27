-- Ported from upstream 014_uv_raw_aggregation (receiver-v1.2.0), renumbered to 021_uv_raw_aggregation because this fork's
-- 010 to 016 are already taken (FORK.md, docs/reference/sqlite-v1.md). SQL unchanged.

update samples
set metadata_json = json_set(metadata_json, '$.aggregation', 'min_max_average')
where type_code = 'uv_exposure'
  and case
      when json_valid(metadata_json) then
          json_extract(metadata_json, '$.sample_kind') = 'raw_quantity'
          and json_extract(metadata_json, '$.aggregation') = 'sum'
      else 0
  end;
