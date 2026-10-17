-- Variant A: no RLS.
DROP POLICY IF EXISTS tenant_session    ON telemetry;
DROP POLICY IF EXISTS tenant_membership ON telemetry;
DROP POLICY IF EXISTS tenant_membership_fn ON telemetry;
ALTER TABLE telemetry DISABLE ROW LEVEL SECURITY;
