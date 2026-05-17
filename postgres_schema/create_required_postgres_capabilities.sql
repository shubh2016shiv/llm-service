-- This file enables only PostgreSQL capabilities that the schema directly uses.
-- Keeping this small makes it clear which database features are required before
-- any business tables are created.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

COMMENT ON EXTENSION pgcrypto IS
    'Required for gen_random_uuid(), which gives every main business record a stable UUID primary key.';
