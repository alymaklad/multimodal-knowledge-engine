import json
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.conversation import ChatMessage, Conversation
from app.models.job import ProcessingJob
from app.processors.base import ProcessorContext
from app.providers.base import ChatProvider, ChatProviderError, ChatRefusedError, EvidenceChunk
from app.services.chat import resolve_citations, run_turn
from app.services.retrieval import RetrievedEvidence
from app.workers.pipeline import process_source_content, start_source_job
from tests.test_processors import make_pdf

client = TestClient(app)
_owners = count(1)


def _owner() -> dict:
    return {"X-User-Id": f"chatter-{next(_owners)}"}


def _add(owner: dict, collection_id: int, filename: str, data: bytes) -> int:
    response = client.post(
        "/api/sources/upload", data={"collection_id": str(collection_id)}, files={"media": (filename, data, "")}, headers=owner
    )
    source_id = response.json()["source_id"]
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
        start_source_job(db, job_id)
        process_source_content(
            db, job_id, context=ProcessorContext(ocr=lambda: None, speech=lambda: None, image_describer=lambda: None)
        )
    return source_id


def _collection(owner: dict) -> tuple[int, int, int]:
    collection_id = client.post("/api/collections", json={"name": "Chat"}, headers=owner).json()["id"]
    paper = _add(
        owner,
        collection_id,
        "fusion.pdf",
        make_pdf(["Overview of search.", "Reciprocal rank fusion merges rankings by summing reciprocal ranks."]),
    )
    notes = _add(owner, collection_id, "gpu.md", b"# Serving\n\nQuantised models fit on a single small GPU.")
    return collection_id, paper, notes


def _conversation(owner: dict, collection_id: int, **payload) -> dict:
    response = client.post(f"/api/collections/{collection_id}/conversations", json=payload, headers=owner)
    assert response.status_code == 201, response.text
    return response.json()


def _events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_a_question_gets_a_cited_answer() -> None:
    owner = _owner()
    collection_id, paper, _ = _collection(owner)
    conversation = _conversation(owner, collection_id)
    assert conversation["title"] == "New conversation"

    turn = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "How does reciprocal rank fusion work?"}, headers=owner
    ).json()
    answer = turn["answer"]
    assert answer["status"] == "complete" and answer["grounded"] is True
    assert "[1]" in answer["content"]
    first = answer["citations"][0]
    assert first["marker"] == 1 and first["source_id"] == paper
    assert first["citation"] in {"p. 2", "pp. 1–2"}
    assert answer["retrieval_query"] == "How does reciprocal rank fusion work?"

    detail = client.get(f"/api/conversations/{conversation['id']}", headers=owner).json()
    assert detail["title"] == "How does reciprocal rank fusion work?"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]


def test_follow_ups_are_searched_with_the_conversation_in_mind() -> None:
    owner = _owner()
    collection_id, _, _ = _collection(owner)
    conversation = _conversation(owner, collection_id)
    url = f"/api/conversations/{conversation['id']}/messages"
    client.post(url, json={"content": "Explain reciprocal rank fusion"}, headers=owner)
    follow_up = client.post(url, json={"content": "And why summing?"}, headers=owner).json()["answer"]
    assert follow_up["retrieval_query"] == "Explain reciprocal rank fusion And why summing?"
    assert follow_up["grounded"] is True


def test_the_answer_streams_as_server_sent_events() -> None:
    owner = _owner()
    collection_id, paper, _ = _collection(owner)
    conversation = _conversation(owner, collection_id)
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream", json={"content": "rank fusion"}, headers=owner
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "user_message" and kinds[1] == "retrieval" and kinds[-1] == "done"
    assert "delta" in kinds
    streamed = "".join(data["text"] for kind, data in events if kind == "delta")
    done = events[-1][1]
    assert done["content"] == streamed.strip()
    assert done["citations"][0]["source_id"] == paper


def test_a_conversation_can_be_limited_to_some_sources() -> None:
    owner = _owner()
    collection_id, paper, notes = _collection(owner)
    conversation = _conversation(owner, collection_id, source_ids=[notes])
    answer = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "rank fusion GPU"}, headers=owner
    ).json()["answer"]
    assert {citation["source_id"] for citation in answer["citations"]} == {notes}

    widened = client.patch(f"/api/conversations/{conversation['id']}", json={"source_ids": None}, headers=owner).json()
    assert widened["source_ids"] is None
    bad = client.patch(f"/api/conversations/{conversation['id']}", json={"source_ids": [999999]}, headers=owner)
    assert bad.status_code == 404


def test_nothing_found_is_said_plainly_and_cites_nothing() -> None:
    owner = _owner()
    collection_id = client.post("/api/collections", json={"name": "Empty"}, headers=owner).json()["id"]
    conversation = _conversation(owner, collection_id)
    answer = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "Anything at all?"}, headers=owner
    ).json()["answer"]
    assert answer["grounded"] is False and answer["citations"] == []
    assert "No saved source" in answer["content"]


def test_conversations_are_private_and_go_with_their_collection() -> None:
    owner, stranger = _owner(), _owner()
    collection_id = client.post("/api/collections", json={"name": "Mine"}, headers=owner).json()["id"]
    conversation = _conversation(owner, collection_id, title="Planning")
    assert client.get(f"/api/conversations/{conversation['id']}", headers=stranger).status_code == 404
    assert client.post(f"/api/collections/{collection_id}/conversations", json={}, headers=stranger).status_code == 404
    assert client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "hi"}, headers=stranger
    ).status_code == 404
    listed = client.get(f"/api/collections/{collection_id}/conversations", headers=owner).json()
    assert [item["title"] for item in listed] == ["Planning"]

    assert client.delete(f"/api/collections/{collection_id}", headers=owner).status_code == 204
    with SessionLocal() as db:
        assert db.get(Conversation, conversation["id"]) is None


def test_a_conversation_answers_one_question_at_a_time() -> None:
    owner = _owner()
    collection_id = client.post("/api/collections", json={"name": "Busy"}, headers=owner).json()["id"]
    conversation = _conversation(owner, collection_id)
    with SessionLocal() as db:
        db.add(ChatMessage(conversation_id=conversation["id"], role="assistant", status="streaming", content=""))
        db.commit()
    busy = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "hi"}, headers=owner)
    assert busy.status_code == 409


class _ScriptedProvider(ChatProvider):
    model = "scripted"

    def __init__(self, parts=(), error: Exception | None = None, rewrite_error: Exception | None = None) -> None:
        self._parts, self._error, self._rewrite_error = list(parts), error, rewrite_error

    def rewrite_query(self, history, question):
        if self._rewrite_error:
            raise self._rewrite_error
        return question

    def stream_answer(self, history, question, evidence):
        yield from self._parts
        if self._error:
            raise self._error


def _conversation_with_evidence() -> int:
    owner = _owner()
    collection_id, _, _ = _collection(owner)
    return _conversation(owner, collection_id)["id"]


@pytest.mark.parametrize(
    ("error", "status", "content"),
    [
        (ChatProviderError("Ollama is not reachable"), "failed", "Partial"),
        (ChatRefusedError("declined"), "refused", "The model declined to answer this question, so no answer was kept."),
    ],
)
def test_failures_are_stored_with_their_reason(error, status, content) -> None:
    conversation_id = _conversation_with_evidence()
    with SessionLocal() as db:
        events = list(run_turn(db, conversation_id, "rank fusion", provider=_ScriptedProvider(["Partial"], error)))
        assert events[-1].type == "error" and events[-1].data["status"] == status
        message = db.get(ChatMessage, events[-1].data["message_id"])
        assert (message.status, message.content) == (status, content)
        assert message.error_message == str(error)


def test_a_disconnected_client_leaves_an_interrupted_answer() -> None:
    conversation_id = _conversation_with_evidence()
    with SessionLocal() as db:
        turn = run_turn(db, conversation_id, "rank fusion", provider=_ScriptedProvider(["First part", " never sent"]))
        for event in turn:
            if event.type == "delta":
                break
        turn.close()
        message = db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant").one()
        assert (message.status, message.content) == ("interrupted", "First part")


def test_a_failed_rewrite_falls_back_to_the_question() -> None:
    conversation_id = _conversation_with_evidence()
    with SessionLocal() as db:
        list(run_turn(db, conversation_id, "rank fusion", provider=_ScriptedProvider(["One [1]."])))
        provider = _ScriptedProvider(["Two [1]."], rewrite_error=ChatProviderError("down"))
        events = list(run_turn(db, conversation_id, "and then?", provider=provider))
        retrieval = next(event for event in events if event.type == "retrieval")
        assert retrieval.data["query"] == "and then?"
        assert events[-1].type == "done"


def _evidence(count_: int):
    chunks = [
        EvidenceChunk(source_id=index, title=f"S{index}", origin_url=None, content_type="text", text=f"text {index}", score=1.0, chunk_key=f"text:{index}", citation=f"p. {index}")
        for index in range(1, count_ + 1)
    ]
    retrieved = [
        RetrievedEvidence(source_id=index, title=f"S{index}", origin_url=None, content_type="text", text=f"text {index}", score=1.0, chunk_key=f"text:{index}", locator={"page": index})
        for index in range(1, count_ + 1)
    ]
    return chunks, retrieved


def test_citation_markers_resolve_in_order_and_invented_ones_disappear() -> None:
    chunks, retrieved = _evidence(3)
    text, citations = resolve_citations("Fusion sums ranks [2]. It is robust [1, 3]. Also [7].", chunks, retrieved)
    assert text == "Fusion sums ranks [2]. It is robust [1][3]. Also."
    assert [citation["marker"] for citation in citations] == [2, 1, 3]
    assert citations[0]["locator"] == {"page": 2} and citations[0]["citation"] == "p. 2"
