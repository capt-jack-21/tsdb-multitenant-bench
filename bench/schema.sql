-- Schema for tsdb-multitenant-bench. Destructive: drops and recreates all tables.
-- Applied by generate.py --apply-schema, or manually: psql -f bench/schema.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

DROP TABLE IF EXISTS telemetry CASCADE;
DROP TABLE IF EXISTS missions CASCADE;
DROP TABLE IF EXISTS devices CASCADE;
DROP TABLE IF EXISTS memberships CASCADE;
DROP TABLE IF EXISTS tenants CASCADE;

CREATE TABLE tenants (
    tenant_id   integer PRIMARY KEY,
    name        text NOT NULL
);

-- Used only by the membership-join RLS policy (E3).
CREATE TABLE memberships (
    user_id     integer NOT NULL,
    tenant_id   integer NOT NULL REFERENCES tenants,
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE devices (
    device_id   integer PRIMARY KEY,
    tenant_id   integer NOT NULL REFERENCES tenants,
    name        text NOT NULL
);

CREATE TABLE missions (
    mission_id  integer PRIMARY KEY,
    tenant_id   integer NOT NULL REFERENCES tenants,
    device_id   integer NOT NULL REFERENCES devices,
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz NOT NULL
);

-- status: 0 idle, 1 takeoff, 2 cruise, 3 landing, 4 fault
-- odometer_m is a monotonically increasing counter per device.
-- No unique constraint: duplicate deliveries are injected on purpose.
CREATE TABLE telemetry (
    time          timestamptz      NOT NULL,
    tenant_id     integer          NOT NULL,
    device_id     integer          NOT NULL,
    mission_id    integer          NOT NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    alt_m         real             NOT NULL,
    battery_pct   real             NOT NULL,
    temp_c        real             NOT NULL,
    status        smallint         NOT NULL,
    odometer_m    double precision NOT NULL
);

-- Chunk interval is a protocol parameter (see PROTOCOL.md). Keep fixed across runs.
SELECT create_hypertable('telemetry', 'time', chunk_time_interval => INTERVAL '1 day');

CREATE INDEX telemetry_tenant_device_time ON telemetry (tenant_id, device_id, time DESC);
CREATE INDEX telemetry_mission_time       ON telemetry (mission_id, time);

-- Non-owner, non-superuser role for measurements. Superusers and table owners
-- bypass RLS, so run queries with:  SET ROLE bench_app;
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bench_app') THEN
        CREATE ROLE bench_app NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO bench_app;
GRANT SELECT, INSERT ON telemetry   TO bench_app;
GRANT SELECT          ON tenants, devices, missions, memberships TO bench_app;
