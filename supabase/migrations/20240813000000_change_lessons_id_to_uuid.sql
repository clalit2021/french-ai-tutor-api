-- Convert lessons.id to UUID and update related references
ALTER TABLE lessons
  ALTER COLUMN id DROP DEFAULT,
  ALTER COLUMN id TYPE uuid USING id::uuid,
  ALTER COLUMN id SET DEFAULT gen_random_uuid(),
  ALTER COLUMN id SET NOT NULL;

-- Update embeddings references
ALTER TABLE embeddings
  DROP CONSTRAINT IF EXISTS embeddings_lesson_id_fkey,
  ALTER COLUMN lesson_id TYPE uuid USING lesson_id::uuid;

ALTER TABLE embeddings
  ADD CONSTRAINT embeddings_lesson_id_fkey FOREIGN KEY (lesson_id)
  REFERENCES lessons (id) ON DELETE CASCADE;
