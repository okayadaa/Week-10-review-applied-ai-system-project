EMBEDDING_MODEL = "gemini-embedding-001"
GENERATIVE_MODEL = "gemini-2.5-flash"
CHROMA_DB_PATH = "./chroma_db"
COLLECTION_NAME = "rag_documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
TOP_K = 3

# Gemini free-tier embedding limit: 100 requests per minute.
# 0.65 s/call ≈ 92 RPM — stays under the cap with a small safety buffer.
# Set to 0 to disable throttling (e.g. in unit tests or paid-tier accounts).
EMBED_DELAY_SECS = 0.65