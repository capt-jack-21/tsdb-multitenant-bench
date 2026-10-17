-- E3 policy 2: membership join.
-- Client must run:  SET ROLE bench_app;  SET app.user_id = '<id>';
DROP POLICY IF EXISTS tenant_session    ON telemetry;
DROP POLICY IF EXISTS tenant_membership ON telemetry;
DROP POLICY IF EXISTS tenant_membership_fn ON telemetry;
ALTER TABLE telemetry ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_membership ON telemetry
    FOR ALL TO bench_app
    USING (tenant_id IN (
        SELECT m.tenant_id FROM memberships m
        WHERE m.user_id = current_setting('app.user_id')::integer))
    WITH CHECK (tenant_id IN (
        SELECT m.tenant_id FROM memberships m
        WHERE m.user_id = current_setting('app.user_id')::integer));
