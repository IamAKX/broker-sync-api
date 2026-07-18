from fastapi.testclient import TestClient

from app.main import create_app


def test_request_response_and_exception_are_logged_at_info_level(capsys):
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    # raise_server_exceptions=False: this test asserts the app gracefully
    # turns an unhandled exception into a logged 500 response — with the
    # default True, TestClient re-raises the original exception into the
    # test even after ServerErrorMiddleware already handled it and sent the
    # response, since Starlette always re-raises post-handling for real ASGI
    # servers to still see/log it. That would fail this test for the wrong
    # reason (an uncaught RuntimeError) instead of exercising the assertions.
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health")
    assert response.status_code == 200

    boom_response = client.get("/boom")
    assert boom_response.status_code == 500

    captured = capsys.readouterr().out
    assert "request_started" in captured
    assert "request_completed" in captured
    assert "request_failed" in captured
