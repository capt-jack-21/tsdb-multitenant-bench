-- Variant E: membership policy written so the planner can use the tenant index.
-- Same logic as variant C (tenants the user belongs to), but the tenant list comes from a STABLE
-- function returning an array, so the policy is `tenant_id = ANY (<stable expression>)`. The
-- planner can then treat it as an index condition instead of a per-row hashed subplan filter.
-- The call is wrapped as `(SELECT app_user_tenants())` so it is evaluated once per query (an
-- InitPlan). A bare function call in a policy can be re-evaluated for every row it filters.
-- Client must run:  SET ROLE bench_app;  SET app.user_id = '<id>';
DROP POLICY IF EXISTS tenant_session       ON telemetry;
DROP POLICY IF EXISTS tenant_membership    ON telemetry;
DROP POLICY IF EXISTS tenant_membership_fn ON telemetry;
ALTER TABLE telemetry ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION app_user_tenants() RETURNS integer[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(array_agg(m.tenant_id), '{}'::integer[])
          FROM memberships m
          WHERE m.user_id = current_setting('app.user_id')::integer $$;

CREATE POLICY tenant_membership_fn ON telemetry
    FOR ALL TO bench_app
    USING      (tenant_id = ANY ((SELECT app_user_tenants())::integer[]))
    WITH CHECK (tenant_id = ANY ((SELECT app_user_tenants())::integer[]));
