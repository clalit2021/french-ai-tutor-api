-- Convert lessons.child_id to UUID and add foreign key
ALTER TABLE lessons
  DROP CONSTRAINT IF EXISTS lessons_child_id_fkey,
  ALTER COLUMN child_id TYPE uuid USING child_id::uuid,
  ALTER COLUMN child_id SET NOT NULL;

ALTER TABLE lessons
  ADD CONSTRAINT lessons_child_id_fkey FOREIGN KEY (child_id)
    REFERENCES public.children(id);
