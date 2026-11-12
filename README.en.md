# AI Portfolio · Three Runnable AI Engineering Projects

[中文](README.md) | English

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-96%20passing-176b4d)
![Offline](https://img.shields.io/badge/demo-offline--first-102b25)
![CI](https://github.com/8hjkkwikl8/ai-portfolio/actions/workflows/ci.yml/badge.svg)

A portfolio for AI application engineering roles: **one flagship retrieval-augmented generation project plus two lightweight projects, each focused on a distinct engineering concern**. Every project ships with complete source code, deterministic demo data, automated tests, and inspectable artifacts. No API key is required by default, and rule-based simulation is never presented as "intelligence".

These are not screenshots or concept mockups. Every metric in this repository can be reproduced with the commands below, and every limitation is stated explicitly in the docs and in the UI.

## Projects at a Glance

| Project | Role | Core engineering problem | Tests | Artifacts |
| --- | --- | --- | --- | --- |
| [InsightRAG](insightrag/) | Flagship · RAG | Hybrid retrieval, evidence replay, sentence-level citation validation, gold-set evaluation | 14 | [Evaluation JSON](insightrag/artifacts/evaluation.json) |
| [PromptLens](promptlens/) | Lightweight · LLM evaluation | Immutable prompt versions, reproducible experiments, failure isolation and retries | 29 | [Comparison report](promptlens/artifacts/report.html) |
| [DataChat BI](datachat_bi/) | Lightweight · AI + data safety | A complete safety boundary from natural language to read-only SQL | 53 | [Chart report](datachat_bi/datachat_report.html) |

Sub-project READMEs are currently in Chinese; commands, configuration keys, and artifact formats are language-independent.

## Quick Start

Requires Python 3.13. All three offline demos use only the standard library; the web UIs and APIs need FastAPI, uvicorn, and httpx.

```bash
python -m pip install -r requirements.txt

# Start both web services and open the dashboard:
# InsightRAG   http://127.0.0.1:8001
# DataChat BI  http://127.0.0.1:8002
python start_all.py

# 96 automated tests + three offline demos; exits non-zero on any failure
python verify_all.py
```

Each project can also run standalone:

```bash
cd insightrag   && python demo.py --eval-output artifacts/evaluation.json
cd promptlens   && python demo.py
cd datachat_bi  && python demo.py
```

`start_all.py` listens on `127.0.0.1` only, checks ports and dependencies before starting, and cleans up only the processes it spawned. `verify_all.py` regenerates the evaluation JSON, the PromptLens comparison report, and the DataChat chart report.

## Project Details

### InsightRAG · Traceable Chinese Knowledge-Base QA

The most common failure of knowledge-base QA is not "no answer" but "a plausible answer with no verifiable basis". InsightRAG separates retrieval, evidence, answering, and metrics into independently inspectable stages:

- Character-offset chunking; every piece of evidence keeps its `source`, `chunk_id`, and original character range, so it can be replayed against the source text.
- BM25 and a lightweight hashed character n-gram vector run in parallel and are fused with RRF; the hashed vector is not presented as a neural semantic embedding.
- Offline extractive answering; with an OpenAI-compatible endpoint configured, online generation is available, but sentences without valid citation IDs are filtered out, and the answer falls back to the offline summary if none remain.
- Gold-set evaluation reports Recall@k, MRR, citation-ID validity, and latency; zero-citation cases are recorded as `N/A` instead of a fake 100%.
- SQLite persistence with whole-source replacement, so re-ingestion never accumulates duplicate corpus.

The demo set contains 3 Chinese policy documents, 13 chunks, and 12 gold questions. Current results: Recall@3 = 1.0, MRR = 1.0, citation-ID validity = 1.0, average latency ≈14 ms (varies by machine). These numbers validate the pipeline only and say nothing about production corpora. See [insightrag/README.md](insightrag/README.md) (Chinese).

### PromptLens · Prompt Versioning and Reproducible Evaluation

If a prompt change is justified only by "it feels better", it can neither be reviewed nor defended. PromptLens pins prompt versions by content hash, datasets by SHA256, and model parameters by a run manifest, then produces comparable metrics and an HTML report:

- Immutable version registry with strict template-variable checks; missing variables abort the run.
- Three transparent metrics — `exact_match`, `contains`, `regex` — with per-tag breakdowns; failed samples score 0 on applicable metrics and never disappear from the denominator.
- Per-item failure isolation, timeouts, and bounded exponential backoff; 408/429/5xx are retryable, other 4xx errors are permanent and not retried.
- Audited fields such as `model` and `messages` cannot be overridden by `model_params`.
- All dynamic report content is HTML-escaped; the report is a single offline-viewable file.

In the bundled example, `exact_match` rises from 7.1% to 92.9%. This comes from a **deterministic mock provider** and only proves the evaluation pipeline works — it is not evidence of real model or prompt quality. Real conclusions require a configured API and re-testing on an independent held-out set. See [promptlens/README.md](promptlens/README.md) (Chinese).

### DataChat BI · Natural Language to Safe SQL

Getting a model to write SQL is easy; ensuring it can only read, only touch allowlisted tables, and never silently drop user-specified filters is the hard part. DataChat BI implements the safety boundary as multiple layers:

- Lexical and structural pre-checks: a single `SELECT`/`WITH` only; comments, multi-statements, write keywords, and non-allowlisted functions are rejected.
- Table, column, and function allowlists, bound parameters, and automatic `LIMIT` injection or tightening.
- Execution uses a read-only URI, `PRAGMA query_only`, the SQLite authorizer, and progress-handler timeouts — bypassing the pre-check is not the end of the story.
- Local JSONL audit events record the question, planner mode, candidate and guarded SQL hashes, guard outcome, row count, and latency; raw SQL, bound parameters, and exception messages are excluded.
- The offline rule planner matches complete question patterns only; extra conditions such as "East China region only" are explicitly rejected rather than silently ignored.
- Chinese web UI with inline SVG charts and no external CDN dependencies.

Demo data: 48 deterministic orders, 7 question types, 6 charts. Total revenue is 612,360.0; the top category by quantity is smart home (智能家居), and the top channel by quantity is Tmall (天猫). See [datachat_bi/README.md](datachat_bi/README.md) (Chinese).

## Shared Design Principles

**Offline-first, not offline-faked intelligence.** Core pipelines run with no network and no keys. Rules, hashing methods, and mocks are labeled as what they are; real models are replaceable adapters, not a prerequisite for the demo.

**Untrusted output must cross a boundary.** Model answers cannot invent sources, candidate SQL cannot bypass the guard, and experiment parameters cannot override audited requests. Every project treats model output as untrusted input.

**Results must be reproducible and refutable.** Original offsets, dataset hashes, run manifests, and per-item outputs are preserved; metric definitions and limitations are shown together. Small demo datasets are a deliberate choice: every result can be checked by hand.

## Repository Layout

```text
ai-portfolio/
├─ index.html              # Portfolio dashboard (open locally)
├─ start_all.py            # Start InsightRAG and DataChat BI with health checks
├─ verify_all.py           # 96 tests + three offline demos
├─ requirements.txt
├─ insightrag/             # Flagship: hybrid retrieval, citation validation, gold-set evaluation
├─ promptlens/             # Lightweight: prompt versioning and reproducible evaluation
└─ datachat_bi/            # Lightweight: guarded NL2SQL analytics
```

## Honesty Boundaries

- InsightRAG's corpus is 3 demo documents; Recall/MRR do not extrapolate to production knowledge bases, and citation-ID validity checks numbering only — it is not semantic faithfulness.
- PromptLens example metrics come from a mock provider and validate the pipeline only; they are not evidence of model quality improvement.
- DataChat BI's 48 orders and rule-based phrasings demonstrate a safety boundary, not general natural-language understanding; the offline mode rejects uncovered phrasings.
- All three projects are single-machine demos without accounts, access control, distributed indexing, or production-grade observability.

## Environment and Dependencies

- Python 3.13 (modern type annotations; older versions are untested).
- FastAPI + uvicorn for the two web services.
- httpx, used only by API tests.
- PromptLens and all three core demos have zero third-party dependencies.
- No external front-end assets; pages, reports, and charts open offline.

## Optional: Connect Real Models

All three projects extend through an OpenAI-compatible endpoint and never go online by default:

```bash
export OPENAI_BASE_URL="https://your-endpoint/v1"
export OPENAI_API_KEY="..."        # May be empty for local compatible servers
export OPENAI_MODEL="your-model"
```

InsightRAG calls online generation only when a query explicitly includes `"online": true`. For PromptLens, set `provider` to `openai_compatible` in the run config. DataChat BI enables its LLM planner when `OPENAI_API_KEY` and `OPENAI_MODEL` are present, but candidate SQL must still pass the same guard and read-only execution layer.
