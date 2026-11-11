import json
import sqlite3

import pytest
from app.bank_index import TOPIC_RULE_VERSION, BankIndexError, index_bank, query_bank


def _bank(*, analysis_text="【分析】本题主要考查一元一次方程的解法。", stem_text="求方程的解。"):
    return {
        "documents": [
            {
                "id": "doc-q1",
                "filename": "synthetic-question.docx",
                "sha256": "a" * 64,
                "role": "question",
                "pair_id": "pair-1",
            },
            {
                "id": "doc-a1",
                "filename": "synthetic-answer.docx",
                "sha256": "b" * 64,
                "role": "answer",
                "pair_id": "pair-1",
            },
        ],
        "pairs": [{"id": "pair-1", "title": "synthetic pair", "question_document_id": "doc-q1"}],
        "questions": [
            {
                "id": "pair-1:q1",
                "pair_id": "pair-1",
                "number": "1",
                "question_type": "solve",
                "section": "一、解答题",
                "stem_blocks": [
                    {
                        "document_id": "doc-q1",
                        "paragraph_index": 4,
                        "text": stem_text,
                        "html": "<p>ignored</p>",
                        "media_paths": [],
                        "ole_ids": [],
                    }
                ],
                "answer_blocks": [],
                "analysis_blocks": [
                    {
                        "document_id": "doc-a1",
                        "paragraph_index": 2,
                        "text": analysis_text,
                        "html": "<p>not used for matching</p>",
                        "media_paths": [],
                        "ole_ids": [],
                    }
                ],
                "structure_status": "imported",
                "issues": [],
                "practice_ready": False,
                "difficulty_score": None,
                "source_question_range": [4, 4],
                "analysis_question_range": [2, 2],
            }
        ],
    }


def test_indexes_positive_source_analysis_with_traceable_evidence(tmp_path):
    original = _bank(
        analysis_text="【分析】本题主要考查一元一次方程的解法，另外涉及函数图象。故先移项。"
    )
    indexed = index_bank(original, tmp_path)
    topics = indexed["questions"][0]["topics"]

    assert [item["label"] for item in topics] == ["一元一次方程", "函数图象"]
    assert all(item["method"] == TOPIC_RULE_VERSION for item in topics)
    assert all(item["source_document_id"] == "doc-a1" for item in topics)
    assert all(item["source_paragraph_index"] == 2 for item in topics)
    assert topics[0]["source_text"] == "【分析】本题主要考查一元一次方程的解法，另外涉及函数图象。"
    # Input is not mutated, and topic rules do not invent readiness or difficulty.
    assert "topics" not in original["questions"][0]
    assert indexed["questions"][0]["practice_ready"] is False
    assert indexed["questions"][0]["difficulty_score"] is None
    assert (tmp_path / "question-bank.sqlite").is_file()


@pytest.mark.parametrize(
    "analysis_text",
    [
        "本题不涉及一次函数。",
        "本题考查的不是函数，而是新定义符号。",
        "本题考查圆周长。",
        "本题考查画圆。",
        "本题考查核心概念，正确选项A项写着反比例函数。",
        "只在本题题干中出现二次函数，不在解析中作考点说明。",
        "本题主要考查新颖方法，不涉及目录中的词语。",
    ],
)
def test_nonpositive_or_option_only_text_does_not_create_topics(tmp_path, analysis_text):
    bank = _bank(analysis_text=analysis_text, stem_text="本题考查二次函数")
    indexed = index_bank(bank, tmp_path)
    assert indexed["questions"][0]["topics"] == []


def test_positive_clause_after_negated_topic_is_still_captured(tmp_path):
    bank = _bank(analysis_text="本题考查的不是函数，而是三角形的周长。")
    indexed = index_bank(bank, tmp_path)
    assert [topic["label"] for topic in indexed["questions"][0]["topics"]] == ["三角形"]


def test_circle_topic_requires_an_explicit_standalone_context(tmp_path):
    bank = _bank(analysis_text="本题考查圆的周长。")
    indexed = index_bank(bank, tmp_path)
    assert [topic["label"] for topic in indexed["questions"][0]["topics"]] == ["圆"]


def test_specialized_source_terms_are_exactly_indexed(tmp_path):
    bank = _bank(
        analysis_text="本题考查根的判别式、根与系数的关系、科学记数法、合并同类项、圆锥的侧面积。"
    )
    indexed = index_bank(bank, tmp_path)
    assert [topic["label"] for topic in indexed["questions"][0]["topics"]] == [
        "根的判别式",
        "根与系数的关系",
        "科学记数法",
        "合并同类项",
        "圆锥的侧面积",
    ]


def test_unmatched_and_untraceable_analysis_produce_empty_topics(tmp_path):
    bank = _bank(analysis_text="本题主要考查一种自定义技巧。")
    bank["questions"][0]["analysis_blocks"][0]["document_id"] = None
    indexed = index_bank(bank, tmp_path)
    assert indexed["questions"][0]["topics"] == []


def test_sqlite_query_filters_and_preserves_complete_question_json(tmp_path):
    bank = _bank()
    second = json.loads(json.dumps(bank["questions"][0]))
    second.update(
        id="pair-1:q2",
        number="2",
        analysis_blocks=[
            {
                "document_id": "doc-a1",
                "paragraph_index": 8,
                "text": "本题涉及全等三角形。",
                "html": "",
                "media_paths": [],
                "ole_ids": [],
            }
        ],
    )
    second["stem_blocks"][0]["text"] = "求三角形边长。"
    bank["questions"].append(second)
    tenth = json.loads(json.dumps(second))
    tenth.update(id="pair-1:q10", number="10", analysis_blocks=[])
    tenth["stem_blocks"][0]["text"] = "计算 10。"
    bank["questions"].append(tenth)
    indexed = index_bank(bank, tmp_path)
    database = tmp_path / "question-bank.sqlite"

    assert [question["id"] for question in query_bank(database, topic="一元一次方程")] == [
        "pair-1:q1"
    ]
    assert [question["id"] for question in query_bank(database, topic="全等三角形")] == [
        "pair-1:q2"
    ]
    assert [
        question["id"] for question in query_bank(database, keyword="求方程", pair_id="pair-1")
    ] == ["pair-1:q1"]
    assert query_bank(database, keyword="%' OR 1=1 --") == []
    assert query_bank(database, pair_id="missing") == []
    assert [q["id"] for q in query_bank(database, pair_id="pair-1")] == [
        "pair-1:q1",
        "pair-1:q2",
        "pair-1:q10",
    ]
    queried = query_bank(database, keyword="求方程")[0]
    assert queried == indexed["questions"][0]
    assert queried["stem_blocks"][0]["html"] == "<p>ignored</p>"

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM documents").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM pairs").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM topics").fetchone() == (2,)


def test_existing_database_survives_failed_rebuild_and_repeat_is_idempotent(tmp_path):
    first = index_bank(_bank(), tmp_path)
    database = tmp_path / "question-bank.sqlite"
    prior_bytes = database.read_bytes()

    broken = _bank()
    broken["questions"].append(dict(broken["questions"][0]))
    with pytest.raises(BankIndexError, match="duplicate question"):
        index_bank(broken, tmp_path)
    assert database.read_bytes() == prior_bytes
    assert query_bank(database) == first["questions"]
    assert not list(tmp_path.glob(".question-bank-*.sqlite"))

    repeated = index_bank(_bank(), tmp_path)
    assert query_bank(database) == repeated["questions"]


def test_invalid_query_limit_and_dangling_pair_are_rejected(tmp_path):
    index_bank(_bank(), tmp_path)
    with pytest.raises(ValueError, match="limit"):
        query_bank(tmp_path / "question-bank.sqlite", limit=True)

    bank = _bank()
    bank["questions"][0]["pair_id"] = "absent"
    with pytest.raises(BankIndexError, match="unknown pair"):
        index_bank(bank, tmp_path)
