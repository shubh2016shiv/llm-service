-- Shared timestamp trigger.
--
-- Many tables keep both created_at and updated_at. The application should not
-- need to remember to update updated_at on every write; the database can do
-- that consistently for any table that attaches this trigger function.

CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION set_updated_at_timestamp() IS
    'Keeps updated_at current on tables that attach this trigger.';
