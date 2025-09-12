-- Ensure foreign key columns have NOT NULL constraints
-- and remove any orphaned rows before applying the constraint.

-- Clean up orphaned children and enforce parent relationship
DELETE FROM children WHERE parent_id IS NULL;
ALTER TABLE children
  ALTER COLUMN parent_id SET NOT NULL;

-- Clean up orphaned embeddings and enforce lesson relationship
DELETE FROM embeddings WHERE lesson_id IS NULL;
ALTER TABLE embeddings
  ALTER COLUMN lesson_id SET NOT NULL;
