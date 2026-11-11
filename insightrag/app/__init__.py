"""InsightRAG：离线优先、可追溯引用的轻量企业知识库检索。"""

__version__ = "1.0.0"

from .chunking import TextChunk, chunk_text
from .store import KnowledgeStore

__all__ = ["KnowledgeStore", "TextChunk", "chunk_text", "__version__"]
