import csv
import hashlib
import os
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pinecone import Pinecone
from pypdf import PdfReader


load_dotenv()

DOCS_DIR = Path(__file__).parent
INDEX_NAME = os.environ["INDEX_NAME"]

# Pinecone free tier: 250k tokens/min. Stay at 60% to leave a safety margin.
SAFE_TOKENS_PER_MIN = 150_000
CHARS_PER_TOKEN = 3  # conservative: overestimates tokens
BATCH_SIZE = 96  # max texts per request for Pinecone's embedding API
MAX_RETRIES = 8


def load_pdf(path: Path) -> list[Document]:
    """Load one LangChain Document per PDF page."""
    reader = PdfReader(path)
    documents = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "source": path.name,
                        "document_type": "pdf",
                        "page": page_number,
                    },
                )
            )
    return documents


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


def load_corpus(folder: Path) -> list[Document]:
    """Load all supported files from the governance corpus."""
    documents = []

    for path in sorted(folder.iterdir()):
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            documents.extend(load_pdf(path))
        elif suffix == ".txt":
            documents.extend(load_text(path))
        elif suffix == ".csv":
            documents.extend(load_csv(path))

    return documents


def stable_chunk_id(doc: Document) -> str:
    """Create a deterministic ID so rerunning ingestion does not duplicate chunks."""
    identity = (
        f"{doc.metadata.get('source', '')}|"
        f"{doc.metadata.get('page', '')}|"
        f"{doc.metadata.get('row', '')}|"
        f"{doc.page_content}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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


def find_existing_ids(index, ids: list[str]) -> set[str]:
    """Check which chunk IDs are already in Pinecone so we don't re-embed them."""
    found = set()
    for start in range(0, len(ids), 50):
        response = index.fetch(ids=ids[start : start + 50])
        found.update(response.vectors.keys())
    return found


def add_batch_with_retry(vector_store, batch) -> None:
    ids = [chunk_id for chunk_id, _ in batch]
    docs = [chunk for _, chunk in batch]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            vector_store.add_documents(documents=docs, ids=ids)
            return
        except Exception as error:
            if is_rate_limit_error(error) and attempt < MAX_RETRIES:
                wait = 30 * attempt
                print(f"  429 from Pinecone, retry {attempt}/{MAX_RETRIES} in {wait}s...")
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    print(f"Loading governance documents from: {DOCS_DIR}")

    documents = load_corpus(DOCS_DIR)
    print(f"Loaded {len(documents)} source documents/pages/rows")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
    )

    chunks = splitter.split_documents(documents)
    print(f"Created {len(chunks)} chunks")

    # Deduplicate by ID (identical chunks would collapse to the same vector anyway)
    chunks_by_id = {stable_chunk_id(chunk): chunk for chunk in chunks}

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(INDEX_NAME)

    existing = find_existing_ids(index, list(chunks_by_id.keys()))
    pending = [(cid, c) for cid, c in chunks_by_id.items() if cid not in existing]
    print(f"{len(existing)} chunks already indexed, {len(pending)} to ingest")

    embeddings = PineconeEmbeddings(model="llama-text-embed-v2")
    vector_store = PineconeVectorStore(index=index, embedding=embeddings)
    limiter = TokenRateLimiter(SAFE_TOKENS_PER_MIN)

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        tokens = sum(estimate_tokens(c.page_content) for _, c in batch)

        limiter.wait_for(tokens)
        add_batch_with_retry(vector_store, batch)

        print(f"Ingested {min(start + BATCH_SIZE, len(pending))}/{len(pending)} chunks")

    print(f"Done. Pinecone index: {INDEX_NAME}")