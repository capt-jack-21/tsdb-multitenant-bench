\set t random(1, :tenants)
\set d random(1, :dpt)
\set dev (:t - 1) * :dpt + :d
SET app.tenant_id = :t \;
SET app.user_id = :t \;
SELECT time, lat, lon, alt_m, battery_pct, temp_c, status, odometer_m FROM telemetry WHERE tenant_id = :t AND device_id = :dev ORDER BY time DESC LIMIT 100;
