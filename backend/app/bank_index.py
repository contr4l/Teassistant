"""Evidence-based source-topic indexing and an atomic SQLite question bank."""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

TOPIC_RULE_VERSION = "source-analysis-rule-v1"

# A deliberately narrow term list for exact source-text matching. These are
# descriptive labels, not IDs from an official curriculum or a model judgment.
_TOPIC_TERMS = (
    "一元一次方程",
    "二元一次方程组",
    "一元二次方程",
    "一元一次不等式组",
    "二次根式",
    "圆锥的侧面积",
    "根的判别式",
    "根与系数的关系",
    "科学记数法",
    "合并同类项",
    "待定系数法",
    "算术平方根",
    "函数图象",
    "反比例函数",
    "一次函数",
    "二次函数",
    "锐角三角函数",
    "全等三角形",
    "相似三角形",
    "平行四边形",
    "勾股定理",
    "圆周角",
    "圆心角",
    "中心对称",
    "轴对称",
    "因式分解",
    "平方根",
    "立方根",
    "有理数",
    "无理数",
    "实数",
    "整式乘法",
    "整式加减",
    "分式方程",
    "分式",
    "二次函数",
    "函数",
    "不等式",
    "方程",
    "三角形",
    "四边形",
    "矩形",
    "菱形",
    "正方形",
    "圆",
    "平行线",
    "旋转",
    "概率",
    "平均数",
    "中位数",
    "众数",
    "方差",
    "统计",
    "比例",
    "坐标系",
    "图形与坐标",
)

_TOPIC_TERMS = tuple(dict.fromkeys(_TOPIC_TERMS))
_TRIGGER_RE = re.compile(r"本题(?:主要)?考查|主要考查|涉及")
_SENTENCE_RE = re.compile(r"(?<=[。！？；;])|[\r\n]+")
_OPTION_RE = re.compile(r"(?:选项|正确选项|答案选项|[ＡＡＢＢＣＣＤＤABCD]\s*[.．、项])")
_NEGATION_RE = re.compile(
    r"(?:不是|并非|不含|不涉及|不考查|不属于|无需|未涉及|未考查|没有涉及|没有考查|非)$"
)
_NEGATED_TRIGGER_RE = re.compile(r"(?:不|未|没有|并非)$")


class BankIndexError(ValueError):
    """The supplied source bank cannot be indexed without losing integrity."""


def _topic_evidence(question: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    blocks = question.get("analysis_blocks", [])
    if not isinstance(blocks, list):
        raise BankIndexError("question analysis_blocks must be a list")

    for block in blocks:
        if not isinstance(block, dict):
            raise BankIndexError("analysis block must be an object")
        raw_text = block.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        document_id = block.get("document_id")
        paragraph_index = block.get("paragraph_index")
        if (
            not isinstance(document_id, str)
            or type(paragraph_index) is not int
            or paragraph_index < 0
        ):
            # Without source coordinates a match cannot be cited, so it is not an index hit.
            continue

        for raw_sentence in _SENTENCE_RE.split(raw_text):
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            for trigger in _TRIGGER_RE.finditer(sentence):
                trigger_prefix = sentence[max(0, trigger.start() - 8) : trigger.start()]
                if _NEGATED_TRIGGER_RE.search(trigger_prefix):
                    continue
                candidate_text = sentence[trigger.end() :]
                option = _OPTION_RE.search(candidate_text)
                if option:
                    candidate_text = candidate_text[: option.start()]
                # Prefer the most specific matched phrase. Generic terms such as
                # “函数” or “三角形” are suppressed when contained by a longer hit.
                hits = [term for term in _TOPIC_TERMS if term in candidate_text]
                specific_hits = sorted(
                    (
                        term
                        for term in hits
                        if not any(term != other and term in other for other in hits)
                    ),
                    key=candidate_text.find,
                )
                for term in specific_hits:
                    occurrence = candidate_text.find(term)
                    previous_char = candidate_text[occurrence - 1 : occurrence]
                    following_char = candidate_text[
                        occurrence + len(term) : occurrence + len(term) + 1
                    ]
                    if term == "圆" and (
                        previous_char in {"画", "作", "描"} or following_char in {"周", "锥"}
                    ):
                        continue
                    preceding = candidate_text[max(0, occurrence - 12) : occurrence].strip()
                    if _NEGATION_RE.search(preceding):
                        continue
                    key = (term, document_id, paragraph_index, sentence)
                    if key in seen:
                        continue
                    seen.add(key)
                    evidence.append(
                        {
                            "label": term,
                            "source_text": sentence,
                            "source_document_id": document_id,
                            "source_paragraph_index": paragraph_index,
                            "method": TOPIC_RULE_VERSION,
                        }
                    )
    return evidence


def _require_list(bank: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = bank.get(field)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise BankIndexError(f"bank {field} must be a list of objects")
    return value


def _unique_ids(records: list[dict[str, Any]], category: str) -> None:
    ids = [record.get("id") for record in records]
    if any(not isinstance(record_id, str) or not record_id for record_id in ids):
        raise BankIndexError(f"every {category} needs a non-empty string id")
    if len(ids) != len(set(ids)):
        raise BankIndexError(f"duplicate {category} id")


def _atomic_write_index(bank: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "question-bank.sqlite"
    fd, temp_name = tempfile.mkstemp(prefix=".question-bank-", suffix=".sqlite", dir=output_dir)
    os.close(fd)
    temporary = Path(temp_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                role TEXT NOT NULL,
                pair_id TEXT
            );
            CREATE TABLE pairs (
                id TEXT PRIMARY KEY,
                record_json TEXT NOT NULL
            );
            CREATE TABLE questions (
                id TEXT PRIMARY KEY,
                pair_id TEXT NOT NULL REFERENCES pairs(id),
                number TEXT NOT NULL,
                question_type TEXT NOT NULL,
                section TEXT NOT NULL,
                search_text TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX questions_pair_number ON questions(pair_id, number, id);
            CREATE TABLE topics (
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                label TEXT NOT NULL,
                source_text TEXT NOT NULL,
                source_document_id TEXT NOT NULL REFERENCES documents(id),
                source_paragraph_index INTEGER NOT NULL,
                method TEXT NOT NULL,
                PRIMARY KEY(question_id, ordinal)
            );
            CREATE INDEX topics_label ON topics(label, question_id);
            """
        )
        # executescript may commit before executing; start the data transaction again.
        connection.execute("BEGIN IMMEDIATE")
        documents = _require_list(bank, "documents")
        pairs = _require_list(bank, "pairs")
        questions = _require_list(bank, "questions")
        _unique_ids(documents, "document")
        _unique_ids(pairs, "pair")
        _unique_ids(questions, "question")

        for document in documents:
            connection.execute(
                "INSERT INTO documents(id, filename, sha256, role, pair_id) VALUES (?, ?, ?, ?, ?)",
                (
                    document["id"],
                    str(document.get("filename", "")),
                    str(document.get("sha256", "")),
                    str(document.get("role", "")),
                    document.get("pair_id"),
                ),
            )
        for pair in pairs:
            connection.execute(
                "INSERT INTO pairs(id, record_json) VALUES (?, ?)",
                (pair["id"], json.dumps(pair, ensure_ascii=False, sort_keys=True)),
            )
        pair_ids = {pair["id"] for pair in pairs}
        document_ids = {document["id"] for document in documents}
        for question in questions:
            pair_id = question.get("pair_id")
            if pair_id not in pair_ids:
                raise BankIndexError(f"question {question['id']} references unknown pair")
            for topic in question["topics"]:
                if topic["source_document_id"] not in document_ids:
                    raise BankIndexError(
                        f"question {question['id']} topic references unknown document"
                    )
            search_parts = []
            for block_field in ("stem_blocks", "answer_blocks", "analysis_blocks"):
                for block in question.get(block_field, []):
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        search_parts.append(block["text"])
            search_parts.extend(topic["label"] for topic in question["topics"])
            search_text = "\n".join(search_parts)
            connection.execute(
                "INSERT INTO questions(id, pair_id, number, question_type, section, search_text, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    question["id"],
                    pair_id,
                    str(question.get("number", "")),
                    str(question.get("question_type", "")),
                    str(question.get("section", "")),
                    search_text,
                    json.dumps(question, ensure_ascii=False, sort_keys=True),
                ),
            )
            for ordinal, topic in enumerate(question["topics"]):
                connection.execute(
                    "INSERT INTO topics(question_id, ordinal, label, source_text, "
                    "source_document_id, source_paragraph_index, method) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        question["id"],
                        ordinal,
                        topic["label"],
                        topic["source_text"],
                        topic["source_document_id"],
                        topic["source_paragraph_index"],
                        topic["method"],
                    ),
                )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise BankIndexError("SQLite index integrity check failed")
        connection.close()
        connection = None
        os.replace(temporary, target)
        return target
    except Exception:
        if connection is not None:
            connection.rollback()
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


def index_bank(bank: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Enrich questions with source-backed topics and atomically rebuild SQLite.

    Topic matches come only from positive, explicitly relevant statements in the
    parsed analysis text. An empty topic list means this rule did not find a
    supported source statement; it does not mean the question has no topic.
    """
    if not isinstance(bank, dict):
        raise BankIndexError("bank must be an object")
    indexed = copy.deepcopy(bank)
    questions = _require_list(indexed, "questions")
    documents = _require_list(indexed, "documents")
    _require_list(indexed, "pairs")
    for document in documents:
        for field in ("filename", "sha256", "role", "pair_id"):
            if not isinstance(document.get(field), str) or not document[field]:
                raise BankIndexError(
                    f"document {document.get('id', '<unknown>')} needs a non-empty {field}"
                )
    for question in questions:
        for field in ("pair_id", "number", "question_type", "section"):
            if field not in question or question[field] is None:
                raise BankIndexError(f"question {question.get('id', '<unknown>')} needs {field}")
        if any(
            not isinstance(question[field], str) or not question[field]
            for field in ("pair_id", "question_type", "section")
        ):
            raise BankIndexError(f"question {question.get('id', '<unknown>')} has invalid metadata")
        if not isinstance(question["number"], (str, int)) or not str(question["number"]):
            raise BankIndexError(f"question {question.get('id', '<unknown>')} has invalid number")
        for field in ("stem_blocks", "answer_blocks", "analysis_blocks"):
            if not isinstance(question.get(field), list) or any(
                not isinstance(block, dict) for block in question[field]
            ):
                raise BankIndexError(f"question {question['id']} {field} must be a list of objects")
        question["topics"] = _topic_evidence(question)
    _atomic_write_index(indexed, Path(output_dir))
    return indexed


def query_bank(
    db_path: Path,
    keyword: str = "",
    topic: str | None = None,
    pair_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return complete enriched question records with bound SQLite filters."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    clauses: list[str] = []
    parameters: list[Any] = []
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("q.search_text LIKE ? ESCAPE '\\'")
        parameters.append(f"%{escaped}%")
    if topic:
        clauses.append("EXISTS (SELECT 1 FROM topics t WHERE t.question_id=q.id AND t.label=?)")
        parameters.append(topic)
    if pair_id:
        clauses.append("q.pair_id=?")
        parameters.append(pair_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT q.record_json FROM questions q"
        + where
        + " ORDER BY q.pair_id, CAST(q.number AS INTEGER), q.number, q.id LIMIT ?"
    )
    parameters.append(limit)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(sql, parameters).fetchall()
    try:
        return [json.loads(row["record_json"]) for row in rows]
    except (TypeError, json.JSONDecodeError):
        raise BankIndexError("SQLite question record is not valid JSON") from None
