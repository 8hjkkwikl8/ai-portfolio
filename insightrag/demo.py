"""零第三方依赖的一键离线演示：构建库、回答问题并运行金标评测。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from app.evaluation import evaluate_cases, load_gold_cases
from app.llm import answer_question
from app.retrieval import HybridRetriever
from app.store import KnowledgeStore


ROOT = Path(__file__).resolve().parent


def run_demo(db_path: str | Path, eval_output: str | Path | None = None) -> dict[str, object]:
    with KnowledgeStore(db_path) as store:
        imported = store.ingest_directory(ROOT / "data" / "sample_docs")
        retriever = HybridRetriever(store)
        questions = [
            "差旅住宿标准和报销时限是什么？",
            "远程办公的核心协作时间是什么？",
            "发现安全事件后应该多久报告？",
        ]
        answers: list[dict[str, object]] = []
        print("=== InsightRAG 离线演示 ===")
        print(f"已导入 {len(imported)} 篇文档，共 {store.stats()['chunks']} 个分块。")
        for question in questions:
            results = retriever.search(question, top_k=3)
            answer = answer_question(question, results, allow_online=False)
            record = {
                "question": question,
                "answer": answer["answer"],
                "sources": [
                    {
                        "citation": source["citation"],
                        "source": source["source"],
                        "chunk_id": source["chunk_id"],
                    }
                    for source in answer["sources"]
                ],
            }
            answers.append(record)
            print("\n" + json.dumps(record, ensure_ascii=False, indent=2))

        cases = load_gold_cases(ROOT / "data" / "eval.jsonl")
        report = evaluate_cases(
            cases, retriever, top_k=3, output_path=eval_output
        )
        print("\n=== 金标评测（离线） ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if eval_output:
            print(f"\n评测 JSON 已保存：{Path(eval_output).resolve()}")
        return {"answers": answers, "evaluation": report}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行 InsightRAG 零依赖离线演示")
    parser.add_argument("--db", help="使用指定本地 SQLite；省略时创建临时数据库并自动清理")
    parser.add_argument("--eval-output", help="可选：将完整评测结果保存为 JSON")
    args = parser.parse_args()
    if args.db:
        run_demo(args.db, args.eval_output)
    else:
        with tempfile.TemporaryDirectory(prefix="insightrag-") as directory:
            run_demo(Path(directory) / "demo.db", args.eval_output)


if __name__ == "__main__":
    main()
