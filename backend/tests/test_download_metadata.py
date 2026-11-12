"""Opaque export IDs must still download with usable names and MIME types."""

import hashlib

import pytest
from app.web_api import create_app
from app.web_store import Store
from fastapi.testclient import TestClient
from test_web import _login, _settings


@pytest.mark.parametrize(
    "suffix,filename,expected_name,mime",
    [
        (".pdf", "student_pdf.pdf", "student_pdf.pdf", "application/pdf"),
        (
            ".docx",
            "student_docx.docx",
            "student_docx.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (".pdf", None, "student_pdf.pdf", "application/pdf"),
        (".pdf", "../wrong.docx", "student_pdf.pdf", "application/pdf"),
    ],
)
def test_export_download_uses_file_metadata_not_opaque_route(
    tmp_path, suffix, filename, expected_name, mime
):
    settings = _settings(tmp_path)
    store = Store(settings.database_url)
    store.init()
    user = store.create_user("synthetic-download", "synthetic-password", "合成下载测试")
    student = store.create_record("student", user["id"], {"name": "合成学生"})
    path = settings.data_dir / ("jobs/synthetic/student" + suffix)
    path.parent.mkdir(parents=True)
    content = b"%PDF-1.4 synthetic metadata test" if suffix == ".pdf" else b"PK synthetic metadata"
    path.write_bytes(content)
    opaque_name = "student_pdf" if suffix == ".pdf" else "student_docx"
    practice = store.create_record(
        "practice",
        user["id"],
        {
            "student_id": student["id"],
            "progress_revision": str(student["revision"]),
            "status": "completed",
            "files": [
                {
                    "name": opaque_name,
                    "filename": filename,
                    "path": str(path.relative_to(settings.data_dir)),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    )
    with TestClient(create_app(settings, store), base_url="https://testserver") as client:
        _login(client, "synthetic-download", "synthetic-password")
        response = client.get(f"/api/practices/{practice['id']}/files/{opaque_name}")
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == mime
        assert f'filename="{expected_name}"' in response.headers["content-disposition"]
    store.close()
