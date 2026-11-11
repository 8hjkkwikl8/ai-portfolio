"""SQLite 文档与分块存储；同 source 导入在单个事务内整体替换。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import threading

from .chunking import chunk_text


SUPPORTED_SUFFIXES = {".txt", ".md"}


@dataclass(frozen=True, slots=True)
class StoredChunk:
    key: int
    source: str
    chunk_id: str
    text: str
    start: int
    end: int
    ordinal: int


class KnowledgeStore(AbstractContextManager["KnowledgeStore"]):
    """线程安全的小型 SQLite 存储，支持文件数据库和 :memory:。"""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if not self.db_path:
            raise ValueError("数据库路径不能为空")
        if self.db_path != ":memory:":
            path = Path(self.db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(path)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    source TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    key INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL REFERENCES documents(source) ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    UNIQUE(source, chunk_id)
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_source_ordinal
                    ON chunks(source, ordinal);
                """
            )

    @staticmethod
    def _validate_document(source: str, content: str) -> tuple[str, str]:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source 不能为空")
        if "\x00" in source:
            raise ValueError("source 不能包含 NUL 字符")
        if not isinstance(content, str):
            raise TypeError("content 必须是字符串")
        if not content.strip():
            raise ValueError("文档内容不能为空")
        return source.strip(), content

    def ingest_document(
        self,
        source: str,
        content: str,
        chunk_size: int = 360,
        overlap: int = 60,
    ) -> dict[str, object]:
        """导入文本；重复 source 删除旧块后写入新块，不产生重复证据。"""
        source, content = self._validate_document(source, content)
        new_chunks = chunk_text(content, source, chunk_size, overlap)
        if not new_chunks:
            raise ValueError("文档未产生可索引文本块")

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            previous = self._conn.execute(
                "SELECT content_hash FROM documents WHERE source = ?", (source,)
            ).fetchone()
            replaced = previous is not None
            unchanged = bool(previous and previous["content_hash"] == digest)
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO documents(source, content, content_hash, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        content = excluded.content,
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at
                    """,
                    (source, content, digest, updated_at),
                )
                self._conn.execute("DELETE FROM chunks WHERE source = ?", (source,))
                self._conn.executemany(
                    """
                    INSERT INTO chunks(
                        source, chunk_id, ordinal, text, start_offset, end_offset
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.source,
                            chunk.chunk_id,
                            chunk.ordinal,
                            chunk.text,
                            chunk.start,
                            chunk.end,
                        )
                        for chunk in new_chunks
                    ],
                )
        return {
            "source": source,
            "chunks": len(new_chunks),
            "characters": len(content),
            "content_hash": digest,
            "replaced": replaced,
            "unchanged": unchanged,
        }

    def ingest_directory(
        self, directory: str | Path, chunk_size: int = 360, overlap: int = 60
    ) -> list[dict[str, object]]:
        """供本地演示/CLI 使用，递归导入 UTF-8 TXT 或 Markdown 文本。"""
        root = Path(directory)
        if not root.is_dir():
            raise FileNotFoundError(f"语料目录不存在：{root}")
        paths = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
        return [
            self.ingest_document(
                path.relative_to(root).as_posix(),
                path.read_text(encoding="utf-8-sig"),
                chunk_size=chunk_size,
                overlap=overlap,
            )
            for path in paths
        ]

    def all_chunks(self) -> list[StoredChunk]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT key, source, chunk_id, ordinal, text, start_offset, end_offset
                FROM chunks ORDER BY source, ordinal, chunk_id
                """
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_document(self, source: str) -> dict[str, object] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT source, content, content_hash, updated_at FROM documents WHERE source = ?",
                (source,),
            ).fetchone()
        return dict(row) if row else None

    def get_chunk(self, source: str, chunk_id: str) -> StoredChunk | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT key, source, chunk_id, ordinal, text, start_offset, end_offset
                FROM chunks WHERE source = ? AND chunk_id = ?
                """,
                (source, chunk_id),
            ).fetchone()
        return self._row_to_chunk(row) if row else None

    def list_sources(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source FROM documents ORDER BY source"
            ).fetchall()
        return [str(row["source"]) for row in rows]

    def stats(self) -> dict[str, int | str]:
        with self._lock:
            documents = self._conn.execute(
                "SELECT COUNT(*) AS count FROM documents"
            ).fetchone()["count"]
            chunks = self._conn.execute(
                "SELECT COUNT(*) AS count FROM chunks"
            ).fetchone()["count"]
            characters = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) AS count FROM documents"
            ).fetchone()["count"]
        return {
            "documents": int(documents),
            "chunks": int(chunks),
            "characters": int(characters),
            "database": self.db_path,
        }

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> StoredChunk:
        return StoredChunk(
            key=int(row["key"]),
            source=str(row["source"]),
            chunk_id=str(row["chunk_id"]),
            text=str(row["text"]),
            start=int(row["start_offset"]),
            end=int(row["end_offset"]),
            ordinal=int(row["ordinal"]),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
