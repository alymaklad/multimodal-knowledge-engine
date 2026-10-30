"""Retrieval evaluation: how often the evidence for an answer contains the passage that answers it.

The corpus in `evals/retrieval/corpus` is ingested into a throwaway database through the real
pipeline (upload, processing, chunking, indexing), then every labelled question in
`evals/retrieval/questions.json` is asked through `retrieve_evidence`, the same function answering
uses. Nothing is mocked except where the data lives, so a change to chunking or retrieval shows up
here as a change in the numbers.

Metrics, per question category:

- **hit@k**: the share of questions whose top-k evidence contains at least one expected passage;
- **recall@k**: the share of expected passages found, which matters for multi-hop questions that need
  two sources;
- **MRR**: the mean of one over the rank of the first expected passage (0 when none is found);
- **coverage@k** (global questions): the share of the expected sources that appear in the evidence.

Run it with `python -m app.evaluation.retrieval`; `--json` writes the full per-question results.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "evals" / "retrieval"
CATEGORIES = ("exact", "paraphrase", "multi_hop", "global")


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    hit: bool
    recall: float
    reciprocal_rank: float
    coverage: float | None
    retrieved: list[str] = field(default_factory=list)


@dataclass
class CategoryScore:
    category: str
    questions: int
    hit: float
    recall: float
    mrr: float
    coverage: float | None


@dataclass
class EvalReport:
    k: int
    configuration: dict[str, str]
    categories: list[CategoryScore]
    results: list[QuestionResult]

    def score(self, category: str) -> CategoryScore:
        return next(score for score in self.categories if score.category == category)

    def table(self) -> str:
        lines = [
            f"Retrieval evaluation, k = {self.k} ("
            + ", ".join(f"{key}={value}" for key, value in self.configuration.items())
            + ")",
            "",
            "| Category | Questions | hit@k | recall@k | MRR | coverage@k |",
            "|---|---|---|---|---|---|",
        ]
        for score in self.categories:
            coverage = f"{score.coverage:.2f}" if score.coverage is not None else "-"
            lines.append(
                f"| {score.category} | {score.questions} | {score.hit:.2f} | {score.recall:.2f} | {score.mrr:.2f} | {coverage} |"
            )
        missed = [result for result in self.results if not result.hit]
        if missed:
            lines += ["", "Missed: " + ", ".join(result.id for result in missed)]
        return "\n".join(lines)


Retriever = Callable[..., list]


def run_retrieval_eval(*, k: int = 8, workdir: Path | None = None, retrieve: Retriever | None = None) -> EvalReport:
    """Ingest the corpus into a fresh database and score every labelled question."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.db.migrate import migrate_database
    from app.models.collection import Collection
    from app.models.job import JobStatus, ProcessingJob
    from app.models.source import SourceKind
    from app.processors.base import ProcessorContext
    from app.services.ingestion import create_uploaded_source
    from app.services.retrieval import retrieve_evidence
    from app.services.storage import SavedUpload
    from app.workers.pipeline import process_source_content, start_source_job

    retrieve = retrieve or retrieve_evidence
    questions = json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))["questions"]
    owned = workdir is None
    workdir = Path(tempfile.mkdtemp(prefix="retrieval-eval-")) if owned else workdir
    engine = create_engine(f"sqlite:///{(workdir / 'eval.db').as_posix()}")
    try:
        migrate_database(engine)
        context = ProcessorContext(ocr=lambda: None, speech=lambda: None, image_describer=lambda: None)
        keys: dict[int, str] = {}
        with Session(engine) as db:
            collection = Collection(user_id="retrieval-eval", name="Retrieval evaluation")
            db.add(collection)
            db.commit()
            for path in sorted((EVAL_DIR / "corpus").glob("*.md")):
                copy = workdir / path.name
                shutil.copyfile(path, copy)
                source = create_uploaded_source(
                    db,
                    user_id="retrieval-eval",
                    upload=SavedUpload(path=str(copy), sha256=f"eval:{path.stem}"),
                    kind=SourceKind.document,
                    title=path.stem,
                    original_filename=path.name,
                    mime_type="text/markdown",
                    collection=collection,
                )
                keys[source.id] = path.stem
                job = db.query(ProcessingJob).filter(ProcessingJob.source_id == source.id).one()
                start_source_job(db, job.id)
                job = process_source_content(db, job.id, context=context)
                if job.status is not JobStatus.completed:
                    raise RuntimeError(f"{path.name} did not process: {job.error_message}")

            results = [
                _score(question, retrieve(db, collection_id=collection.id, query=question["question"], limit=k), keys)
                for question in questions
            ]
    finally:
        engine.dispose()
        if owned:
            shutil.rmtree(workdir, ignore_errors=True)

    settings = get_settings()
    configuration = {
        "embeddings": settings.embedding_provider,
        "reranker": settings.reranker,
        "graph": settings.graph_provider,
        "global routing": "on" if settings.route_global_questions else "off",
    }
    return EvalReport(k=k, configuration=configuration, categories=_summarise(results), results=results)


def _score(question: dict, evidence: list, keys: dict[int, str]) -> QuestionResult:
    retrieved = [(keys.get(item.source_id, "?"), item.text.lower()) for item in evidence]
    if question["category"] == "global":
        expected_sources = question["expected_sources"]
        present = {source for source, _ in retrieved}
        coverage = sum(source in present for source in expected_sources) / len(expected_sources)
        return QuestionResult(
            id=question["id"],
            category="global",
            question=question["question"],
            hit=coverage > 0,
            recall=coverage,
            reciprocal_rank=1.0 if retrieved and retrieved[0][0] in expected_sources else 0.0,
            coverage=coverage,
            retrieved=[source for source, _ in retrieved],
        )

    def matches(expected: dict, item: tuple[str, str]) -> bool:
        return item[0] == expected["source"] and expected["contains"].lower() in item[1]

    found = [expected for expected in question["expected"] if any(matches(expected, item) for item in retrieved)]
    first = next(
        (rank for rank, item in enumerate(retrieved, start=1) if any(matches(expected, item) for expected in question["expected"])),
        None,
    )
    return QuestionResult(
        id=question["id"],
        category=question["category"],
        question=question["question"],
        hit=bool(found),
        recall=len(found) / len(question["expected"]),
        reciprocal_rank=1.0 / first if first else 0.0,
        coverage=None,
        retrieved=[source for source, _ in retrieved],
    )


def _summarise(results: list[QuestionResult]) -> list[CategoryScore]:
    scores = []
    for category in CATEGORIES + ("all",):
        group = [result for result in results if category in ("all", result.category)]
        if not group:
            continue
        coverages = [result.coverage for result in group if result.coverage is not None]
        scores.append(
            CategoryScore(
                category=category,
                questions=len(group),
                hit=mean(result.hit for result in group),
                recall=mean(result.recall for result in group),
                mrr=mean(result.reciprocal_rank for result in group),
                coverage=mean(coverages) if coverages else None,
            )
        )
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score retrieval on the labelled evaluation corpus.")
    parser.add_argument("--k", type=int, default=8, help="evidence passages per question (default 8)")
    parser.add_argument("--json", type=Path, help="also write per-question results to this file")
    args = parser.parse_args(argv)

    # The evaluation never touches the configured database or object store.
    scratch = Path(tempfile.mkdtemp(prefix="retrieval-eval-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(scratch / 'unused.db').as_posix()}")
    os.environ["MEDIA_ROOT"] = str(scratch / "media")
    os.environ["OBJECT_STORE_ROOT"] = str(scratch / "objects")
    os.environ["OBJECT_STORE"] = "local"
    os.environ["VECTOR_STORE"] = "database"
    try:
        report = run_retrieval_eval(k=args.k, workdir=scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(report.table())
    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
