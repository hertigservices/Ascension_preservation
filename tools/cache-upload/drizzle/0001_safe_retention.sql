ALTER TABLE submissions ADD COLUMN objects_deleted integer NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN storage_version integer NOT NULL DEFAULT 0;
