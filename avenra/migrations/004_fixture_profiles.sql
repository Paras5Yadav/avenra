ALTER TABLE environments ADD COLUMN fixture_profile TEXT NOT NULL DEFAULT 'legacy' CHECK(fixture_profile IN ('legacy','banking'));
