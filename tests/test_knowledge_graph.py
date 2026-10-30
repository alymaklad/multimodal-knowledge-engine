from itertools import count
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.main import app
from app.models.graph import Entity, EntityMention, EntityRelation
from app.models.job import ProcessingJob
from app.models.source import Source
from app.processors.base import ProcessorContext
from app.providers.base import GraphExtraction, GraphExtractor
from app.providers.graph_local import ExtractiveSummarizer, RuleGraphExtractor
from app.providers.graph_models import ClaudeGraphExtractor
from app.services.chunking import build_chunks
from app.services.graph import build_source_graph
from app.services.retrieval import _graph_chunks, is_global_question, retrieve_evidence
from app.workers.pipeline import process_source_content, start_source_job

client = TestClient(app)
_owners = count(1)


def _owner() -> dict:
    return {"X-User-Id": f"graph-{next(_owners)}"}


def _add(owner: dict, collection_id: int, filename: str, text: str) -> int:
    response = client.post(
        "/api/sources/upload",
        data={"collection_id": str(collection_id)},
        files={"media": (filename, text.encode(), "")},
        headers=owner,
    )
    assert response.status_code == 202, response.text
    source_id = response.json()["source_id"]
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
        start_source_job(db, job_id)
        process_source_content(
            db, job_id, context=ProcessorContext(ocr=lambda: None, speech=lambda: None, image_describer=lambda: None)
        )
    return source_id


def _collection(owner: dict) -> int:
    return client.post("/api/collections", json={"name": "Graph"}, headers=owner).json()["id"]


QDRANT = "# Qdrant\n\nQdrant is a vector database written in Rust. It indexes vectors with HNSW and filters on payloads.\n"
HNSW = (
    "# Approximate search\n\nThe usual index for approximate nearest-neighbour search is HNSW, a layered graph "
    "proposed by Malkov and Yashunin. Searches start in the sparse top layer and descend.\n"
)
COOKING = "# Bread\n\nA sourdough starter needs flour and water every day. Bake the loaf in a hot oven for forty minutes.\n"


def test_the_rule_extractor_finds_names_and_identifiers_but_not_grammar() -> None:
    text = (
        "# Dense retrievers\n\nDense Passage Retrieval, or DPR, was introduced by Karpukhin and colleagues. "
        "It uses two BERT encoders. Training needs hard negatives, and monoBERT reranks with nDCG gains.\n"
    )
    names = {entity.name for entity in RuleGraphExtractor().extract(text).entities}
    assert {"Dense Passage Retrieval", "DPR", "Karpukhin", "BERT", "monoBERT", "nDCG"} <= names
    # Headings and capitalised sentence openers are not entities.
    assert not names & {"Dense", "It", "Training", "Dense retrievers"}


def test_a_name_that_only_opens_sentences_is_kept_when_it_titles_a_heading() -> None:
    names = {entity.name for entity in RuleGraphExtractor().extract(QDRANT).entities}
    assert {"Qdrant", "Rust", "HNSW"} <= names


def test_entities_named_in_one_sentence_are_linked() -> None:
    relations = RuleGraphExtractor().extract(QDRANT).relations
    pairs = {frozenset((relation.subject, relation.object)) for relation in relations}
    assert frozenset(("Qdrant", "Rust")) in pairs


def test_extractive_summaries_quote_whole_sentences_of_the_source() -> None:
    summary = ExtractiveSummarizer().summarize("HNSW", [HNSW]).text
    assert summary
    assert "#" not in summary
    for sentence in summary.split(". "):
        assert sentence.rstrip(".") in HNSW.replace("\n", " ")


def test_indexing_builds_a_graph_whose_mentions_point_at_chunks() -> None:
    owner = _owner()
    collection_id = _collection(owner)
    qdrant = _add(owner, collection_id, "qdrant.md", QDRANT)
    hnsw = _add(owner, collection_id, "hnsw.md", HNSW)
    with SessionLocal() as db:
        entity = db.scalar(select(Entity).where(Entity.user_id == owner["X-User-Id"], Entity.normalized == "hnsw"))
        assert entity is not None
        mentions = db.scalars(select(EntityMention).where(EntityMention.entity_id == entity.id)).all()
        # One entity shared by both sources, each mention naming the chunk it came from.
        assert {mention.source_id for mention in mentions} == {qdrant, hnsw}
        assert all(mention.chunk_key.startswith("text:") for mention in mentions)
        assert db.scalars(select(EntityRelation).where(EntityRelation.source_id == qdrant)).first() is not None

    source = client.get(f"/api/sources/{hnsw}", headers=owner).json()
    assert "Malkov and Yashunin" in source["summary"]


def test_deleting_a_source_drops_the_entities_only_it_named() -> None:
    owner = _owner()
    collection_id = _collection(owner)
    _add(owner, collection_id, "qdrant.md", QDRANT)
    hnsw = _add(owner, collection_id, "hnsw.md", HNSW)
    assert client.delete(f"/api/sources/{hnsw}", headers=owner).status_code == 204
    with SessionLocal() as db:
        names = set(db.scalars(select(Entity.normalized).where(Entity.user_id == owner["X-User-Id"])))
    assert "hnsw" in names and "qdrant" in names
    assert "malkov" not in names


def test_the_graph_reaches_a_passage_that_shares_no_words_with_the_question_but_a_linked_entity() -> None:
    owner = _owner()
    collection_id = _collection(owner)
    _add(owner, collection_id, "qdrant.md", QDRANT)
    hnsw = _add(owner, collection_id, "hnsw.md", HNSW)
    cooking = _add(owner, collection_id, "bread.md", COOKING)
    with SessionLocal() as db:
        scope = [source.id for source in db.scalars(select(Source).where(Source.user_id == owner["X-User-Id"]))]
        ranked = _graph_chunks(db, scope, "Who designed the index Qdrant relies on?", None)
    sources = [chunk.source_id for chunk in ranked]
    # Qdrant is named; the HNSW passage is reached through the Qdrant-HNSW link.
    assert hnsw in sources
    assert cooking not in sources


def test_global_questions_are_recognised() -> None:
    for question in (
        "What are the main themes?",
        "Give me an overview",
        "Summarize all my sources",
        "What topics do these documents cover?",
    ):
        assert is_global_question(question), question
    for question in ("Who proposed HNSW?", "What is the main idea of BM25 saturation in k1?"):
        assert not is_global_question(question), question


def test_a_global_question_draws_evidence_from_every_source() -> None:
    owner = _owner()
    collection_id = _collection(owner)
    ids = {
        _add(owner, collection_id, "qdrant.md", QDRANT),
        _add(owner, collection_id, "hnsw.md", HNSW),
        _add(owner, collection_id, "bread.md", COOKING),
    }
    with SessionLocal() as db:
        evidence = retrieve_evidence(
            db, collection_id=collection_id, query="What are the main topics across my sources?", limit=3
        )
    assert {item.source_id for item in evidence} == ids


def test_a_failing_extractor_leaves_the_source_indexed_without_a_graph() -> None:
    class Broken(GraphExtractor):
        def extract(self, text: str) -> GraphExtraction:
            raise RuntimeError("model unavailable")

    owner = _owner()
    collection_id = _collection(owner)
    source_id = _add(owner, collection_id, "qdrant.md", QDRANT)
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        stats = build_source_graph(db, source, build_chunks(source), extractor=Broken(), summarizer=ExtractiveSummarizer())
        db.commit()
        assert stats.mentions == 0 and stats.summarized
        assert db.scalars(select(EntityMention).where(EntityMention.source_id == source_id)).first() is None


def test_the_claude_extractor_reads_its_structured_output() -> None:
    payload = '{"entities": [{"name": "BEIR", "kind": "dataset"}, {"name": "DPR", "kind": "method"}], "relations": [{"subject": "BEIR", "label": "evaluated", "object": "DPR"}]}'
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=payload)])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
    extraction = ClaudeGraphExtractor(model="claude-opus-5-5", client=fake).extract("BEIR evaluated DPR.")
    assert [entity.name for entity in extraction.entities] == ["BEIR", "DPR"]
    assert extraction.relations[0].label == "evaluated"
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert calls[0]["fallbacks"] == "default"


def test_retrieval_quality_does_not_regress_on_the_evaluation_corpus() -> None:
    from app.evaluation.retrieval import run_retrieval_eval

    assert get_settings().graph_provider == "local"
    report = run_retrieval_eval(k=3)
    # Measured with hashing embeddings, no reranker, and the local graph; see HOW_IT_WORKS.md.
    assert report.score("exact").hit == 1.0
    assert report.score("multi_hop").hit == 1.0
    assert report.score("multi_hop").recall >= 0.6
    assert report.score("all").hit >= 0.95
