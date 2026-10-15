-- Range guards for rows the batch contract already bounds (RELAY-S13, #133).
-- SQLite cannot add a CHECK to an existing table without rebuilding it, so
-- these are BEFORE INSERT/UPDATE triggers: no table rebuild, existing rows
-- are not rescanned, and a violating write aborts with an integrity error.

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

create trigger if not exists workouts_interval_order_insert
before insert on workouts
when new.end_time < new.start_time
begin
    select raise(abort, 'workout interval reversed');
end;

create trigger if not exists workouts_interval_order_update
before update on workouts
when new.end_time < new.start_time
begin
    select raise(abort, 'workout interval reversed');
end;

create trigger if not exists workouts_duration_range_insert
before insert on workouts
when new.duration_seconds < 0
begin
    select raise(abort, 'workout duration negative');
end;

create trigger if not exists workouts_duration_range_update
before update on workouts
when new.duration_seconds < 0
begin
    select raise(abort, 'workout duration negative');
end;

create trigger if not exists workouts_energy_range_insert
before insert on workouts
when new.energy_kcal is not null and new.energy_kcal < 0
begin
    select raise(abort, 'workout energy negative');
end;

create trigger if not exists workouts_energy_range_update
before update on workouts
when new.energy_kcal is not null and new.energy_kcal < 0
begin
    select raise(abort, 'workout energy negative');
end;

create trigger if not exists workouts_distance_range_insert
before insert on workouts
when new.distance_meters is not null and new.distance_meters < 0
begin
    select raise(abort, 'workout distance negative');
end;

create trigger if not exists workouts_distance_range_update
before update on workouts
when new.distance_meters is not null and new.distance_meters < 0
begin
    select raise(abort, 'workout distance negative');
end;

create trigger if not exists sleep_sessions_interval_order_insert
before insert on sleep_sessions
when new.end_time < new.start_time
begin
    select raise(abort, 'sleep session interval reversed');
end;

create trigger if not exists sleep_sessions_interval_order_update
before update on sleep_sessions
when new.end_time < new.start_time
begin
    select raise(abort, 'sleep session interval reversed');
end;

create trigger if not exists sleep_stage_intervals_interval_order_insert
before insert on sleep_stage_intervals
when new.end_time < new.start_time
begin
    select raise(abort, 'sleep stage interval reversed');
end;

create trigger if not exists sleep_stage_intervals_interval_order_update
before update on sleep_stage_intervals
when new.end_time < new.start_time
begin
    select raise(abort, 'sleep stage interval reversed');
end;

create trigger if not exists electrocardiograms_interval_order_insert
before insert on electrocardiograms
when new.end_time < new.start_time
begin
    select raise(abort, 'electrocardiogram interval reversed');
end;

create trigger if not exists electrocardiograms_interval_order_update
before update on electrocardiograms
when new.end_time < new.start_time
begin
    select raise(abort, 'electrocardiogram interval reversed');
end;
