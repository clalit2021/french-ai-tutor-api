-- Enable pgvector extension and update embeddings table for vector similarity
CREATE EXTENSION IF NOT EXISTS vector;

-- Change embeddings.embedding to pgvector type
ALTER TABLE embeddings
  ALTER COLUMN embedding TYPE vector(1536);

-- Optional: create similarity search index on embedding column
CREATE INDEX IF NOT EXISTS embeddings_embedding_ivfflat_idx
  ON embeddings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
