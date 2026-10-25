from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services.chunking import build_chunks, describe_locator, merge_locators


def _segment(sequence: int, text: str, modality: str = "text", **locator):
    return SimpleNamespace(sequence=sequence, text=text, modality=modality, locator=locator)


def _source(segments=(), transcript=None, ocr=(), visual=None, extracted=(), title="Doc"):
    return SimpleNamespace(
        title=title,
        author=None,
        description=None,
        segments=list(segments),
        transcript=transcript,
        ocr_results=list(ocr),
        visual_analysis=visual,
        extracted_content=list(extracted),
    )


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setenv("CHUNK_MAX_CHARACTERS", "60")
    monkeypatch.setenv("CHUNK_OVERLAP_CHARACTERS", "15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_small_consecutive_pages_are_packed_with_a_page_range() -> None:
    chunks = build_chunks(_source([_segment(1, "Page one text.", page=1), _segment(2, "Page two text.", page=2)]))
    assert [chunk.chunk_key for chunk in chunks] == ["text:1", "summary"]
    assert chunks[0].text == "Page one text.\n\nPage two text."
    assert chunks[0].locator == {"page": 1, "page_end": 2}
    assert describe_locator(chunks[0].locator) == "pp. 1–2"


def test_chunks_overlap_and_stay_within_the_limit(small_chunks) -> None:
    sentences = [_segment(index + 1, f"Sentence number {index} about retrieval.", page=index + 1) for index in range(4)]
    chunks = [chunk for chunk in build_chunks(_source(sentences)) if chunk.chunk_key != "summary"]
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 60 for chunk in chunks)
    # Each chunk after the first begins with the end of the one before it.
    for previous, current in zip(chunks, chunks[1:]):
        assert current.text.split("\n\n")[0] in previous.text


def test_an_overlong_passage_is_split_into_overlapping_windows(small_chunks) -> None:
    words = " ".join(f"word{index}" for index in range(40))
    chunks = [chunk for chunk in build_chunks(_source([_segment(1, words, page=3)])) if chunk.chunk_key != "summary"]
    assert len(chunks) > 2
    assert all(len(chunk.text) <= 60 and chunk.locator == {"page": 3} for chunk in chunks)
    joined = " ".join(chunk.text for chunk in chunks)
    assert all(f"word{index}" in joined for index in range(40))
    # Windows end at word boundaries, so no word is cut in half.
    assert all(token.startswith("word") for chunk in chunks for token in chunk.text.split())


def test_sections_and_streams_are_never_mixed() -> None:
    chunks = build_chunks(
        _source(
            [
                _segment(1, "Intro.", section="Intro"),
                _segment(2, "Setup.", section="Setup"),
                _segment(3, "Text read from a scan.", "ocr", page=4),
            ]
        )
    )
    assert [(chunk.chunk_key, chunk.locator.get("section")) for chunk in chunks[:3]] == [
        ("text:1", "Intro"),
        ("text:2", "Setup"),
        ("ocr:1", None),
    ]


def test_video_evidence_keeps_its_timestamps() -> None:
    transcript = SimpleNamespace(
        text="unused",
        segments=[{"start": 0.0, "end": 4.0, "text": "Brace your core."}, {"start": 4.0, "end": 9.5, "text": "Drive up."}],
    )
    ocr = [
        SimpleNamespace(id=index, text="3 x 12 EACH LEG", timestamp_seconds=float(index)) for index in range(3)
    ] + [SimpleNamespace(id=9, text="REST 60s", timestamp_seconds=5.0)]
    visual = SimpleNamespace(description="A person performs a split squat.")
    extracted = [SimpleNamespace(content_type="workout_exercise", text_representation="Split squat 3x12")]
    chunks = {chunk.chunk_key: chunk for chunk in build_chunks(_source(transcript=transcript, ocr=ocr, visual=visual, extracted=extracted))}

    assert chunks["transcript:1"].text == "Brace your core. Drive up."
    assert chunks["transcript:1"].locator == {"start": 0.0, "end": 9.5}
    # A caption on screen for three frames is one span, not three copies.
    assert chunks["ocr:1"].text == "3 x 12 EACH LEG\nREST 60s"
    assert chunks["ocr:1"].locator == {"start": 0.0, "end": 5.0}
    assert chunks["visual:1"].content_type == "visual"
    assert chunks["extracted:workout_exercise"].content_type == "workout_exercise"
    assert "Title: Doc" in chunks["summary"].text


@pytest.mark.parametrize(
    ("locator", "label"),
    [
        ({"page": 4}, "p. 4"),
        ({"page": 3, "page_end": 5, "section": "Methods"}, "pp. 3–5 · § Methods"),
        ({"start": 750.0, "end": 825.4}, "12:30–13:45"),
        ({"start": 3725.0, "end": 3725.0}, "1:02:05"),
        ({"section": "Guide > Install", "paragraph": 2}, "§ Guide > Install"),
        ({"paragraph": 2}, "¶ 2"),
        ({"region": "image"}, "image"),
        ({}, None),
    ],
)
def test_locators_have_short_readable_labels(locator, label) -> None:
    assert describe_locator(locator) == label


def test_merging_locators_spans_them() -> None:
    merged = merge_locators([{"start": 60.0, "end": 90.0}, {"start": 30.0, "end": 45.0}, {"section": "A"}])
    assert merged == {"start": 30.0, "end": 90.0, "section": "A"}
