import pytest
from app.bank_view import _safe_fragment, render_library


@pytest.mark.parametrize(
    "fragment",
    [
        "<script>alert(1)</script>",
        '<img src="https://example.com/private">',
        '<img src="media/../secret.png">',
        '<img src="media/..\\..\\secret.png">',
        '<img src="media/%2e%2e/secret.png">',
        '<img src="media/a&#10;b.png">',
        '<img src="media/a.png" onerror="alert(1)">',
        '<span style="background:url(https://example.com)">x</span>',
        '<math href="https://example.com"><mi>x</mi></math>',
        "<math><mtext><script>alert(1)</script></mtext></math>",
        '<math><mi style="background:url(https://example.com)">x</mi></math>',
        '<math xmlns="https://example.com"><mi>x</mi></math>',
        '<math><mi mathvariant="bold">x</mi></math>',
    ],
)
def test_source_preview_rejects_active_or_external_content(fragment):
    with pytest.raises(ValueError):
        _safe_fragment(fragment)


def test_source_preview_preserves_local_formula_and_table():
    fragment = (
        '<table><tr><td colspan="2">x<img src="media/doc/formula.png" width="25"></td></tr></table>'
    )
    assert _safe_fragment(fragment) == fragment


def test_mathml_preview_preserves_native_fraction_and_root():
    fragment = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>x</mi>'
        "<msqrt><mn>2</mn></msqrt></mfrac></math>"
    )
    assert _safe_fragment(fragment) == fragment


def test_library_escapes_titles_and_preserves_math_preview(tmp_path):
    bank = {
        "pairs": [{"id": "p", "title": "<script>not executable</script>"}],
        "documents": [],
        "questions": [
            {
                "id": "q",
                "pair_id": "p",
                "number": 1,
                "question_type": "选择题",
                "topics": [{"label": "方程"}],
                "stem_blocks": [{"text": "x<1", "html": 'x&lt;1<img src="media/doc/formula.png">'}],
                "answer_blocks": [],
                "analysis_blocks": [],
                "issues": [],
                "association_status": "unverified",
            }
        ],
    }
    bank["batch_status"] = "partial"
    bank["failed_documents"] = [{"filename": "<script>failed</script>.docx"}]
    path = render_library(bank, tmp_path)
    page = path.read_text()
    assert "<script>not executable</script>" not in page
    assert "&lt;script&gt;not executable&lt;/script&gt;" in page
    assert 'src="media/doc/formula.png"' in page
    assert "JSON.parse(a.dataset.topics)" in page
    assert 'data-batch-status="partial"' in page
    assert "题答来源待进一步自动核验" in page
    assert "&lt;script&gt;failed&lt;/script&gt;.docx" in page
