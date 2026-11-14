"""Chrome regressions using the built frontend and synthetic intercepted APIs only.

Run `make check-web` first, then `.venv/bin/python scripts/check_frontend_interactions.py`.
Requires locally installed official Google Chrome and Playwright; no real accounts,
models, backend, server, browser profile, or external network are used.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://teassistant.test"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def question(identifier):
    return dict(id=identifier, number=identifier, title=f"合成题{identifier}", status="eligible",
                block_reasons=[], point_ids=[], prerequisite_ids=[], difficulty_score=50,
                adjusted=False, disabled=False, revision=1, question_html="<p>合成题干</p>",
                answer_html="<p>合成答案</p>", analysis_html="", history=[], adjustment=None, ai_assessment=None)


class Fixture:
    def __init__(self, browser, dist, *, status="completed", mobile=False):
        self.dist = dist
        self.status = status
        self.practice_ready = False
        self.practice_created = False
        self.has_exam_job = True
        self.calls = []
        self.errors = []
        self.pending = {}
        self.hold = set()
        self.fail_job = False
        self.corrections = []
        self.context = browser.new_context(viewport={"width": 390 if mobile else 1440, "height": 900})
        self.context.route("**/*", self.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def student(self, identifier):
        return dict(id=identifier, name=f"合成学生{identifier}", school="合成学校", grade=9,
                    learned_point_ids=["p1"], progress_revision=1)

    def job(self, identifier="j1", status=None):
        return dict(id=identifier, kind="analysis", resource_id="eA", status=status or self.status,
                    error="合成失败" if (status or self.status) == "failed" else None, attempts=1, result=None)

    def exam(self, identifier):
        return dict(id=f"e{identifier}", student_id=identifier, title=f"合成考试{identifier}", exam_date="2026-11-14",
                    question_count=1, assets=[dict(id="a1", role="paper", filename="synthetic.png", ordinal=0),
                                              dict(id="a2", role="response", filename="synthetic-response.png", ordinal=1)],
                    analysis_job=(self.job() if self.has_exam_job else None) if identifier == "A" else self.job("jB", "completed"))

    def analysis(self):
        evidence = dict(page_id="response", kind="handwriting", bbox=[0, 0, 1, 1])
        return dict(pages=[], teacher_report={}, analysis=dict(revision="synthetic-revision", expected_question_numbers=[1],
            questions=[dict(number=1, association=dict(response_ids=["r1"], status="linked"), point_ids=["p1"], outcome="unknown")],
            paper_questions=[dict(number=1, stem="合成原题", sources=[])],
            student_responses=[dict(id="r1", number=1, part_path="", transcript="合成作答", legibility="readable", sources=[evidence])]))

    def route(self, route):
        url = urlsplit(route.request.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            route.abort()
            return
        path = url.path
        if path.startswith("/api/"):
            self.calls.append((route.request.method, path))
            if path in self.hold:
                self.pending.setdefault(path, []).append(route)
                return
            status, data = self.response(route.request.method, path, route.request)
            route.fulfill(status=status, json=data)
            return
        target = (self.dist / unquote(path).lstrip("/")).resolve()
        if path == "/":
            target = self.dist / "index.html"
        if not target.is_relative_to(self.dist) or not target.is_file():
            route.fulfill(status=404, body="missing synthetic asset")
            return
        route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def response(self, method, path, request):
        if path == "/api/auth/me":
            return 200, dict(user=dict(id="synthetic-admin", username="synthetic", display_name="合成教师", role="admin"), csrf_token="synthetic")
        if path == "/api/students":
            return 200, [self.student("A"), self.student("B")]
        if path == "/api/curriculum":
            return 200, dict(version="synthetic", points=[dict(id="p1", name="合成考点", prerequisite_ids=[])])
        if path.endswith("/exams") and path.startswith("/api/students/"):
            return 200, [self.exam(path.split("/")[3])]
        if path.endswith("/practices") and path.startswith("/api/students/"):
            if path.split("/")[3] == "A" and self.practice_created:
                return 200, [dict(id="prA", job_id="pj1", student_id="A", status="completed" if self.practice_ready else "running", files=[], warnings=[], feedback=None)]
            return 200, []
        if path == "/api/capabilities":
            return 200, dict(allow_codex=False)
        if path == "/api/inventory":
            return 200, dict(points=[dict(point_id="p1", name="合成考点", candidate_count=20, by_difficulty=dict(basic=10, intermediate=10))])
        if path.endswith("/analysis"):
            return (200, self.analysis()) if self.status in {"completed", "partial"} else (409, dict(detail="analysis is not available yet"))
        if path.endswith("/assets") and method == "POST":
            return 201, dict(id="synthetic-new-asset")
        if path.endswith("/corrections"):
            self.corrections.extend(request.post_data_json["corrections"])
            return 200, self.job("c1", "completed")
        if path == "/api/jobs/c1":
            return 200, self.job("c1", "completed")
        if path == "/api/exams/eA/analyze":
            self.has_exam_job = True
            self.status = "running"
            return 200, self.job()
        if path == "/api/jobs/j1/retry":
            self.status = "completed"
            return 200, self.job()
        if path == "/api/jobs/j1":
            if self.fail_job:
                self.fail_job = False
                return 503, dict(detail="synthetic temporary outage")
            return 200, self.job()
        if path == "/api/practices" and method == "POST":
            self.practice_created = True
            return 200, dict(id="prA", job_id="pj1", status="queued")
        if path == "/api/jobs/pj1":
            return 200, self.job("pj1", "completed" if self.practice_ready else "running")
        if path == "/api/practices/prA":
            return 200, dict(id="prA", student_id="A", status="completed", snapshot=None, preview=None, warnings=[], feedback=None,
                             files=[dict(id="f1", label="合成学生A专属试卷", url="/api/practices/prA/files/f1")])
        if path == "/api/admin/users":
            return 200, []
        if path == "/api/admin/bank":
            return 200, dict(revision=1, configured=True, question_count=2, eligible_count=2, blocked_count=0,
                             disabled_count=0, source_count=1, points=[], active_version="synthetic", history=[], imports=[])
        if path == "/api/admin/bank/questions":
            return 200, dict(revision=1, total=2, items=[question("A"), question("B")])
        if path.startswith("/api/admin/bank/questions/"):
            return 200, question(path.rsplit("/", 1)[-1])
        return 404, dict(detail="synthetic endpoint not configured")

    def open(self, hash_path):
        self.page.goto(ORIGIN + "/#" + hash_path)

    def close(self):
        assert not self.errors, self.errors
        self.context.close()


def run(browser, dist):
    passed = []
    for status in ("queued", "running"):
        f = Fixture(browser, dist, status=status)
        f.open("/exam/eA?s=A")
        expect(f.page.locator(".job-progress")).to_contain_text("排队" if status == "queued" else "处理中")
        expect(f.page.locator(".analysis-controls button").first).to_be_disabled()
        f.status = "completed"
        expect(f.page.get_by_text("合成作答", exact=True)).to_be_visible()
        assert ("GET", "/api/jobs/j1") in f.calls
        f.close()
        passed.append(f"resume_{status}_to_completed")

    f = Fixture(browser, dist, status="running")
    f.fail_job = True
    f.open("/exam/eA?s=A")
    expect(f.page.get_by_role("button", name="重新读取进度")).to_be_visible()
    f.status = "completed"
    f.page.get_by_role("button", name="重新读取进度").click()
    expect(f.page.get_by_text("合成作答", exact=True)).to_be_visible()
    assert not any(method == "POST" and path.endswith("/analyze") for method, path in f.calls)
    f.close()
    passed.append("progress_read_failure_retries_subscription_only")

    f = Fixture(browser, dist, status="running")
    f.open("/exam/eA?s=A")
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.open("/")
    f.page.locator(".home-exams select").select_option("B")
    expect(f.page.get_by_text("合成考试B", exact=True)).to_be_visible()
    before = f.calls.count(("GET", "/api/jobs/j1"))
    f.status = "completed"
    f.page.wait_for_timeout(1600)
    expect(f.page.get_by_text("合成考试B", exact=True)).to_be_visible()
    expect(f.page.get_by_text("合成考试A", exact=True)).to_have_count(0)
    assert f.calls.count(("GET", "/api/jobs/j1")) == before
    f.close()
    passed.append("analysis_unmount_stops_poll_and_keeps_student_B")

    # Two late stages: the submit response itself and a result GET after polling.
    for late in ("/api/practices", "/api/practices/prA"):
        f = Fixture(browser, dist)
        f.practice_ready = True
        f.hold.add(late)
        f.open("/practice?s=A")
        f.page.locator(".exam-signal input").check()
        f.page.locator(".inventory-row input[type=checkbox]").check()
        f.page.get_by_role("button", name="生成练习卷", exact=True).click()
        for _ in range(30):
            if f.pending.get(late):
                break
            f.page.wait_for_timeout(50)
        assert f.pending.get(late), f.calls
        f.page.locator("#practice-form select").first.select_option("B")
        expect(f.page.locator("#practice-form select").first).to_have_value("B")
        for route in f.pending[late]:
            code, body = f.response(route.request.method, late, route.request)
            route.fulfill(status=code, json=body)
        f.page.wait_for_timeout(300)
        expect(f.page.get_by_role("link", name="合成学生A专属试卷")).to_have_count(0)
        assert "s=B" in f.page.url
        f.close()
        passed.append("practice_student_fence_at_" + late.rsplit("/", 1)[-1])

    f = Fixture(browser, dist, status="running")
    f.has_exam_job = False
    f.open("/exam/eA?s=A")
    f.page.get_by_role("button", name="开始分析", exact=True).click()
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.open("/")
    expect(f.page.locator(".home-exams")).to_be_visible()
    before = f.calls.count(("GET", "/api/jobs/j1"))
    f.open("/exam/eA?s=A")
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.status = "completed"
    expect(f.page.get_by_text("合成作答", exact=True)).to_be_visible()
    assert f.calls.count(("GET", "/api/jobs/j1")) > before
    assert f.calls.count(("POST", "/api/exams/eA/analyze")) == 1
    f.close()
    passed.append("new_analysis_job_resumes_after_same_app_return")

    f = Fixture(browser, dist)
    f.open("/practice?s=A")
    f.page.locator(".exam-signal input").check()
    f.page.locator(".inventory-row input[type=checkbox]").check()
    f.page.get_by_role("button", name="生成练习卷", exact=True).click()
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.page.locator("#practice-form select").first.select_option("B")
    expect(f.page.locator("#practice-form select").first).to_have_value("B")
    before = f.calls.count(("GET", "/api/jobs/pj1"))
    f.page.locator("#practice-form select").first.select_option("A")
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.practice_ready = True
    expect(f.page.get_by_role("link", name="合成学生A专属试卷")).to_be_visible()
    assert f.calls.count(("GET", "/api/jobs/pj1")) > before
    assert f.calls.count(("POST", "/api/practices")) == 1
    f.close()
    passed.append("pending_practice_resumes_from_student_history")

    f = Fixture(browser, dist)
    f.practice_created = True
    f.hold.add("/api/students/A/practices")
    f.open("/practice?s=A")
    f.page.locator(".exam-signal input").check()
    f.page.locator(".inventory-row input[type=checkbox]").check()
    expect(f.page.get_by_role("button", name="生成练习卷", exact=True)).to_be_disabled()
    for route in f.pending["/api/students/A/practices"]:
        code, body = f.response("GET", "/api/students/A/practices", route.request)
        route.fulfill(status=code, json=body)
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.practice_ready = True
    expect(f.page.get_by_role("link", name="合成学生A专属试卷")).to_be_visible()
    assert ("POST", "/api/practices") not in f.calls
    f.close()
    passed.append("slow_history_blocks_duplicate_practice_submission")

    f = Fixture(browser, dist, status="running")
    f.open("/exam/eA?s=A")
    expect(f.page.locator(".job-progress")).to_contain_text("处理中")
    f.status = "failed"
    f.page.get_by_role("button", name="重试", exact=True).click()
    expect(f.page.get_by_text("合成作答", exact=True)).to_be_visible()
    assert f.calls.count(("POST", "/api/jobs/j1/retry")) == 1
    f.close()
    passed.append("failed_analysis_remains_retryable")

    f = Fixture(browser, dist, mobile=True)
    f.open("/exam/eA?s=A")
    expect(f.page.get_by_text("合成作答", exact=True)).to_be_visible()
    assert f.page.evaluate("!window.isSecureContext && typeof crypto.randomUUID === 'undefined'")
    upload = f.page.locator(".upload-role-card input[type=file]").first
    upload.set_input_files([dict(name="synthetic-1.png", mimeType="image/png", buffer=PNG)])
    f.page.wait_for_timeout(300)
    assert ("POST", "/api/exams/eA/assets") in f.calls
    f.page.get_by_role("button", name="修正识别", exact=True).click()
    f.page.get_by_role("button", name="提交修正", exact=True).click()
    f.page.wait_for_timeout(300)
    assert len(f.corrections) == 1 and f.corrections[0]["id"]
    f.close()
    passed.append("insecure_http_phone_upload_and_correction")

    f = Fixture(browser, dist)
    f.hold.add("/api/admin/bank/questions/A")
    f.open("/admin")
    f.page.get_by_role("button", name="题库", exact=True).click()
    f.page.locator(".bank-question-row").filter(has_text="合成题A").click()
    f.page.wait_for_timeout(100)
    f.page.locator(".bank-question-row").filter(has_text="合成题B").click()
    expect(f.page.locator(".bank-detail-heading h2")).to_contain_text("合成题B")
    draft = f.page.locator(".bank-adjust-form input[maxlength='500']")
    draft.fill("合成B未保存调整")
    f.page.locator(".bank-question-row").filter(has_text="合成题B").click()
    expect(draft).to_have_value("合成B未保存调整")
    for route in f.pending["/api/admin/bank/questions/A"]:
        route.fulfill(json=question("A"))
    f.page.wait_for_timeout(200)
    expect(f.page.locator(".bank-detail-heading h2")).to_contain_text("合成题B")
    expect(draft).to_have_value("合成B未保存调整")
    f.close()
    passed.append("bank_stale_detail_preserves_selection_and_draft")

    f = Fixture(browser, dist)
    f.open("/admin")
    f.page.get_by_role("button", name="题库", exact=True).click()
    f.page.locator(".bank-question-row").filter(has_text="合成题A").click()
    expect(f.page.locator(".bank-detail-heading h2")).to_contain_text("合成题A")
    f.page.locator(".bank-adjust-form input[maxlength='500']").fill("合成A调整")
    f.hold.update({"/api/admin/bank/questions/A", "/api/admin/bank/questions/B"})
    f.page.get_by_role("button", name="保存调整", exact=True).click()
    f.page.wait_for_timeout(100)
    f.page.locator(".bank-question-row").filter(has_text="合成题B").click()
    f.page.wait_for_timeout(100)
    for route in f.pending["/api/admin/bank/questions/A"]:
        assert route.request.method == "PATCH"
        route.fulfill(status=409, json=dict(detail="synthetic revision conflict"))
    f.page.wait_for_timeout(150)
    for route in f.pending["/api/admin/bank/questions/B"]:
        route.fulfill(json=question("B"))
    expect(f.page.locator(".bank-detail-heading h2")).to_contain_text("合成题B")
    f.close()
    passed.append("old_bank_save_failure_does_not_stall_new_detail")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend/dist")
    args = parser.parse_args()
    dist = args.dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("build the frontend first with make check-web")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            passed = run(browser, dist)
            print(json.dumps(dict(synthetic_only=True, browser="Google Chrome", passed=passed, count=len(passed)), ensure_ascii=False, indent=2))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
