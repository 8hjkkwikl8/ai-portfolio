"""FastAPI 服务与 ``python -m app.api`` 启动入口。"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
import time
from typing import Any

from .embeddings import build_embedding_vectorizer_from_env
from .evaluation import GoldCase, evaluate_cases, load_gold_cases
from .llm import LLMConfig, answer_question
from .retrieval import HybridRetriever
from .store import KnowledgeStore

try:  # 核心检索与测试不依赖 FastAPI。
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - 由无第三方环境覆盖
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    HTMLResponse = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    Field = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample_docs"
EVAL_PATH = PROJECT_ROOT / "data" / "eval.jsonl"
STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


if BaseModel is not object:

    class IngestRequest(BaseModel):
        source: str = Field(min_length=1, max_length=200)
        content: str = Field(min_length=1)
        chunk_size: int = Field(default=360, gt=0, le=4000)
        overlap: int = Field(default=60, ge=0, le=1000)


    class QueryRequest(BaseModel):
        question: str = Field(min_length=1, max_length=2000)
        top_k: int = Field(default=5, gt=0, le=20)
        online: bool = False


    class EvaluationCaseRequest(BaseModel):
        question: str = Field(min_length=1, max_length=2000)
        expected_sources: list[str] = Field(min_length=1)


    class EvaluateRequest(BaseModel):
        top_k: int = Field(default=5, gt=0, le=20)
        cases: list[EvaluationCaseRequest] | None = None

else:  # 让无 FastAPI 环境仍可导入本模块并运行核心功能。
    IngestRequest = Any  # type: ignore[misc,assignment]
    QueryRequest = Any  # type: ignore[misc,assignment]
    EvaluationCaseRequest = Any  # type: ignore[misc,assignment]
    EvaluateRequest = Any  # type: ignore[misc,assignment]


def _default_database() -> str:
    return os.environ.get(
        "INSIGHTRAG_DB", str(PROJECT_ROOT / "data" / "insightrag.db")
    )


def create_app(
    db_path: str | Path | None = None, load_samples: bool = True
) -> Any:
    if FastAPI is None:
        raise RuntimeError("启动 API 需要先安装 requirements.txt 中的 fastapi 与 uvicorn")

    store = KnowledgeStore(db_path or _default_database())
    if load_samples and int(store.stats()["documents"]) == 0 and SAMPLE_DIR.is_dir():
        store.ingest_directory(SAMPLE_DIR)
    # 未配置 INSIGHTRAG_EMBEDDING_* 时返回 None，保持离线哈希向量且不联网。
    retriever = HybridRetriever(
        store, vectorizer=build_embedding_vectorizer_from_env()
    )

    @asynccontextmanager
    async def lifespan(_: Any):
        yield
        store.close()

    service = FastAPI(
        title="InsightRAG",
        description="离线优先的企业知识库混合检索与可追溯问答",
        version="1.0.0",
        lifespan=lifespan,
    )

    @service.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "offline_ready": True,
            "online_configured": LLMConfig.from_env().configured,
            **store.stats(),
        }

    @service.post("/api/ingest")
    def ingest(payload: IngestRequest) -> dict[str, object]:
        # 刻意不接收 path/url：API 不能把任意服务器文件或远程地址当作语料读取。
        if len(payload.content) > 2_000_000:
            raise HTTPException(status_code=413, detail="单篇文档不能超过 200 万字符")
        if payload.overlap >= payload.chunk_size:
            raise HTTPException(status_code=422, detail="overlap 必须小于 chunk_size")
        try:
            result = store.ingest_document(
                payload.source,
                payload.content,
                chunk_size=payload.chunk_size,
                overlap=payload.overlap,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, **result, **store.stats()}

    @service.post("/api/query")
    def query(payload: QueryRequest) -> dict[str, object]:
        started = time.perf_counter()
        try:
            results = retriever.search(payload.question, top_k=payload.top_k)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        answer = answer_question(
            payload.question,
            results,
            allow_online=payload.online,
            config=LLMConfig.from_env(),
        )
        return {
            "question": payload.question,
            **answer,
            "results": [item.to_dict() for item in results],
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    @service.post("/api/evaluate")
    def evaluate(payload: EvaluateRequest | None = None) -> dict[str, object]:
        payload = payload or EvaluateRequest()
        try:
            if payload.cases is None:
                cases = load_gold_cases(EVAL_PATH)
            else:
                cases = [
                    GoldCase.from_dict(
                        {
                            "question": item.question,
                            "expected_sources": item.expected_sources,
                        }
                    )
                    for item in payload.cases
                ]
            return evaluate_cases(cases, retriever, top_k=payload.top_k)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @service.get("/api/stats")
    def stats() -> dict[str, object]:
        vectorizer_name = retriever.vectorizer.name
        notice = (
            "哈希向量是确定性特征工程，不是神经嵌入。"
            if vectorizer_name.startswith("hash-ngram")
            else "已启用神经嵌入适配器；语义召回质量取决于所接模型与数据。"
        )
        return {
            **store.stats(),
            "sources": store.list_sources(),
            "retrieval": f"BM25 + {vectorizer_name} 向量余弦 + RRF",
            "embedding_provider": vectorizer_name,
            "embedding_notice": notice,
            "online_configured": LLMConfig.from_env().configured,
        }

    @service.get("/", response_class=HTMLResponse)
    def index() -> Any:
        return HTMLResponse(STATIC_INDEX.read_text(encoding="utf-8"))

    return service


app = create_app() if FastAPI is not None else None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="启动 InsightRAG 中文知识库服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", help="SQLite 路径，也可用 INSIGHTRAG_DB 设置")
    parser.add_argument("--no-samples", action="store_true", help="空库时不导入中文样例")
    args = parser.parse_args()
    if FastAPI is None:
        parser.error("缺少依赖，请先执行：python -m pip install -r requirements.txt")
    import uvicorn

    service = (
        app
        if args.db is None and not args.no_samples
        else create_app(args.db or _default_database(), load_samples=not args.no_samples)
    )
    uvicorn.run(service, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
