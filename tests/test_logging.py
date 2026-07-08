from fastapi.testclient import TestClient

from app.main import create_app


def test_request_response_and_exception_are_logged_at_info_level(capsys):
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200

    boom_response = client.get("/boom")
    assert boom_response.status_code == 500

    captured = capsys.readouterr().out
    assert "request_started" in captured
    assert "request_completed" in captured
    assert "request_failed" in captured
