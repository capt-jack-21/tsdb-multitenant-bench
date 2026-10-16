-- Variant B / E3 policy 1: session setting.
-- Client must run:  SET ROLE bench_app;  SET app.tenant_id = '<id>';
DROP POLICY IF EXISTS tenant_session    ON telemetry;
DROP POLICY IF EXISTS tenant_membership ON telemetry;
ALTER TABLE telemetry ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_session ON telemetry
    FOR ALL TO bench_app
    USING      (tenant_id = current_setting('app.tenant_id')::integer)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::integer);
