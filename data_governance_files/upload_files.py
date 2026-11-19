import csv
import hashlib
import json
import os
import re
import time
from collections import deque
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pinecone import Pinecone
from pypdf import PdfReader


load_dotenv()

DOCS_DIR = Path(__file__).parent
INDEX_NAME = os.environ["INDEX_NAME"]
EMBED_MODEL = "llama-text-embed-v2"  # 1024 dimensions -> index dimension must be 1024
CHUNK_CACHE = DOCS_DIR / ".semantic_chunks_cache.json"

# Pinecone free tier: 250k tokens/min. Stay at 60% to leave a safety margin.
SAFE_TOKENS_PER_MIN = 150_000
CHARS_PER_TOKEN = 3  # conservative: overestimates tokens
BATCH_SIZE = 96  # max texts per request for Pinecone's embedding API
MAX_RETRIES = 8

# --- Semantic chunking knobs ---
# Each sentence is embedded together with BUFFER neighbours on each side, then we
# cut wherever the distance between consecutive sentences is in the top
# (100 - BREAKPOINT_PERCENTILE)% for that document. Lower percentile = more, smaller chunks.
BUFFER = 1
BREAKPOINT_PERCENTILE = 90
MIN_CHUNK_CHARS = 300  # don't cut a chunk until it has at least this much text
MAX_CHUNK_CHARS = 4000  # ~1300 tokens: well under the model's 2048-token input limit

# Sentence ends (. ? ! followed by whitespace) or blank lines (paragraph breaks).
SENTENCE_BOUNDARY = re.compile(r"(?<=[.?!])\s+|\n\s*\n")

CHUNK_CONFIG = f"{EMBED_MODEL}|{BUFFER}|{BREAKPOINT_PERCENTILE}|{MIN_CHUNK_CHARS}|{MAX_CHUNK_CHARS}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_pdf(path: Path) -> list[Document]:
    """Load the whole PDF as one Document so chunks can span page breaks.

    `_page_starts` records where each page begins in the joined text, so every
    chunk can still be tagged with the page it starts on.
    """
    reader = PdfReader(path)
    parts, page_starts, offset = [], [], 0

    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        page_starts.append((offset, page_number))
        parts.append(text)
        offset += len(text) + 1  # +1 for the "\n" joiner below

    if not parts:
        return []

    return [
        Document(
            page_content="\n".join(parts),
            metadata={
                "source": path.name,
                "document_type": "pdf",
                "_page_starts": page_starts,
            },
        )
    ]


def load_text(path: Path) -> list[Document]:
    """Load a plain-text policy document."""
    return [
        Document(
            page_content=path.read_text(encoding="utf-8"),
            metadata={
                "source": path.name,
                "document_type": "text",
            },
        )
    ]


def load_csv(path: Path) -> list[Document]:
    """Turn each catalog row into a readable Document."""
    documents = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row_number, row in enumerate(reader, start=2):
            content = "\n".join(f"{key}: {value}" for key, value in row.items())

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": path.name,
                        "document_type": "data_catalog",
                        "row": row_number,
                        "field_name": row.get("field_name", ""),
                    },
                )
            )

    return documents


LOADERS = {".pdf": load_pdf, ".txt": load_text, ".csv": load_csv}


# ---------------------------------------------------------------------------
# Rate limiting / retries
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


class TokenRateLimiter:
    """Sliding 60-second window that blocks until a batch fits under the cap."""

    def __init__(self, max_tokens: int, window: float = 60.0):
        self.max_tokens = max_tokens
        self.window = window
        self.events = deque()  # (timestamp, tokens)

    def wait_for(self, tokens: int) -> None:
        while True:
            now = time.monotonic()
            while self.events and now - self.events[0][0] >= self.window:
                self.events.popleft()

            used = sum(t for _, t in self.events)
            if not self.events or used + tokens <= self.max_tokens:
                self.events.append((now, tokens))
                return

            wait = self.window - (now - self.events[0][0]) + 0.5
            print(f"  Rate limit window full ({used} tokens used), waiting {wait:.0f}s...")
            time.sleep(max(wait, 1))


def is_rate_limit_error(error: Exception) -> bool:
    message = str(error)
    return (
        "429" in message
        or "Too Many Requests" in message
        or "RESOURCE_EXHAUSTED" in message
    )


def with_retry(fn):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as error:
            if is_rate_limit_error(error) and attempt < MAX_RETRIES:
                wait = 30 * attempt
                print(f"  429 from Pinecone, retry {attempt}/{MAX_RETRIES} in {wait}s...")
                time.sleep(wait)
            else:
                raise


class RateLimitedEmbeddings(Embeddings):
    """Wraps PineconeEmbeddings so EVERY embedding call goes through the limiter.

    Semantic chunking embeds every sentence, and ingestion embeds every chunk;
    both share the same token budget, so both use this wrapper.
    """

    def __init__(self, inner: Embeddings, limiter: TokenRateLimiter):
        self.inner = inner
        self.limiter = limiter

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            self.limiter.wait_for(sum(estimate_tokens(t) for t in batch))
            vectors.extend(with_retry(lambda: self.inner.embed_documents(batch)))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        self.limiter.wait_for(estimate_tokens(text))
        return with_retry(lambda: self.inner.embed_query(text))


# ---------------------------------------------------------------------------
# Semantic chunking
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character spans of each sentence in `text`."""
    spans, start = [], 0
    for match in SENTENCE_BOUNDARY.finditer(text):
        if match.start() > start:
            spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def semantic_spans(text: str, embeddings: Embeddings) -> list[tuple[int, int]]:
    """Group sentences into chunks, cutting where the topic shifts.

    Returns (start, end) character spans into `text`.
    """
    spans = split_sentences(text)
    if len(spans) <= 2:
        return [(spans[0][0], spans[-1][1])] if spans else []

    sentences = [text[s:e] for s, e in spans]

    # Embed each sentence with its neighbours for a less noisy signal.
    windows = [
        " ".join(sentences[max(0, i - BUFFER) : i + BUFFER + 1])
        for i in range(len(sentences))
    ]
    vectors = np.array(embeddings.embed_documents(windows), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    # distances[i] = cosine distance between sentence i and sentence i + 1
    distances = 1.0 - np.sum(vectors[:-1] * vectors[1:], axis=1)
    threshold = np.percentile(distances, BREAKPOINT_PERCENTILE)

    chunks, first = [], 0  # `first` = index of the chunk's first sentence
    for i, distance in enumerate(distances):
        current_size = spans[i][1] - spans[first][0]
        size_with_next = spans[i + 1][1] - spans[first][0]

        topic_shift = distance > threshold and current_size >= MIN_CHUNK_CHARS
        too_big = size_with_next > MAX_CHUNK_CHARS

        if topic_shift or too_big:
            chunks.append((spans[first][0], spans[i][1]))
            first = i + 1

    chunks.append((spans[first][0], spans[-1][1]))

    # Fold a tiny trailing chunk into the previous one if it fits.
    if len(chunks) > 1:
        last_start, last_end = chunks[-1]
        prev_start, _ = chunks[-2]
        if last_end - last_start < MIN_CHUNK_CHARS and last_end - prev_start <= MAX_CHUNK_CHARS:
            chunks[-2:] = [(prev_start, last_end)]

    return chunks


def page_for_offset(page_starts: list, offset: int) -> int:
    page = page_starts[0][1]
    for start, number in page_starts:
        if start > offset:
            break
        page = number
    return page


def chunk_document(doc: Document, embeddings: Embeddings, fallback) -> list[Document]:
    text = doc.page_content
    page_starts = doc.metadata.get("_page_starts")
    base_metadata = {k: v for k, v in doc.metadata.items() if not k.startswith("_")}

    # CSV rows are already one self-contained record each: no semantic split.
    if doc.metadata.get("document_type") == "data_catalog":
        spans = [(0, len(text))]
    else:
        spans = semantic_spans(text, embeddings)

    chunks = []
    for start, end in spans:
        chunk_text = text[start:end].strip()
        if not chunk_text:
            continue

        # A single huge "sentence" (e.g. a table with no periods) can still exceed
        # the cap; split those by size as a last resort.
        pieces = fallback.split_text(chunk_text) if len(chunk_text) > MAX_CHUNK_CHARS else [chunk_text]

        for piece in pieces:
            metadata = dict(base_metadata)
            if page_starts:
                local = chunk_text.find(piece[:80])
                metadata["page"] = page_for_offset(page_starts, start + max(local, 0))
            chunks.append(Document(page_content=piece, metadata=metadata))

    return chunks


def file_cache_key(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{path.name}|{digest}|{CHUNK_CONFIG}"


def chunk_corpus(folder: Path, embeddings: Embeddings) -> list[Document]:
    """Load and semantically chunk every supported file.

    Chunking costs embedding tokens, so results are cached per file (keyed on
    file contents + chunking settings). Re-runs only re-chunk files that changed.
    """
    old_cache = json.loads(CHUNK_CACHE.read_text(encoding="utf-8")) if CHUNK_CACHE.exists() else {}
    new_cache = {}
    fallback = RecursiveCharacterTextSplitter(chunk_size=MAX_CHUNK_CHARS, chunk_overlap=200)
    all_chunks = []

    for path in sorted(folder.iterdir()):
        loader = LOADERS.get(path.suffix.lower())
        if loader is None:
            continue

        key = file_cache_key(path)
        if key in old_cache:
            file_chunks = [Document(**c) for c in old_cache[key]]
            print(f"  {path.name}: {len(file_chunks)} chunks (cached)")
        else:
            print(f"  {path.name}: chunking...")
            file_chunks = []
            for doc in loader(path):
                file_chunks.extend(chunk_document(doc, embeddings, fallback))
            print(f"  {path.name}: {len(file_chunks)} chunks")

        new_cache[key] = [
            {"page_content": c.page_content, "metadata": c.metadata} for c in file_chunks
        ]
        # Save after every file so an interrupted run keeps its progress.
        CHUNK_CACHE.write_text(json.dumps({**old_cache, **new_cache}, ensure_ascii=False), encoding="utf-8")
        all_chunks.extend(file_chunks)

    # Drop entries for files that were deleted or changed.
    CHUNK_CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    return all_chunks


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def stable_chunk_id(doc: Document) -> str:
    """Create a deterministic ID so rerunning ingestion does not duplicate chunks."""
    identity = (
        f"{doc.metadata.get('source', '')}|"
        f"{doc.metadata.get('page', '')}|"
        f"{doc.metadata.get('row', '')}|"
        f"{doc.page_content}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def find_existing_ids(index, ids: list[str]) -> set[str]:
    """Check which chunk IDs are already in Pinecone so we don't re-embed them."""
    found = set()
    for start in range(0, len(ids), 50):
        response = index.fetch(ids=ids[start : start + 50])
        found.update(response.vectors.keys())
    return found


if __name__ == "__main__":
    limiter = TokenRateLimiter(SAFE_TOKENS_PER_MIN)
    embeddings = RateLimitedEmbeddings(PineconeEmbeddings(model=EMBED_MODEL), limiter)

    print(f"Semantic chunking governance documents from: {DOCS_DIR}")
    chunks = chunk_corpus(DOCS_DIR, embeddings)
    print(f"Created {len(chunks)} chunks")

    # Deduplicate by ID (identical chunks would collapse to the same vector anyway)
    chunks_by_id = {stable_chunk_id(chunk): chunk for chunk in chunks}

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(INDEX_NAME)

    existing = find_existing_ids(index, list(chunks_by_id.keys()))
    pending = [(cid, c) for cid, c in chunks_by_id.items() if cid not in existing]
    print(f"{len(existing)} chunks already indexed, {len(pending)} to ingest")

    # Rate limiting and retries now live inside `embeddings`.
    vector_store = PineconeVectorStore(index=index, embedding=embeddings)

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vector_store.add_documents(
            documents=[chunk for _, chunk in batch],
            ids=[chunk_id for chunk_id, _ in batch],
        )
        print(f"Ingested {min(start + BATCH_SIZE, len(pending))}/{len(pending)} chunks")

    print(f"Done. Pinecone index: {INDEX_NAME}")