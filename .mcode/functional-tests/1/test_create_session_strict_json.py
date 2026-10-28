"""POST /create-checkout-session strict JSON body parsing (express.json() strict parity)."""

import requests

from conftest import record


def _post_raw(base, body: bytes):
    return requests.post(
        f"{base}/create-checkout-session",
        data=body,
        headers={"Content-Type": "application/json"},
        allow_redirects=False,
        timeout=30,
    )


def _assert_bad_request(r):
    assert r.status_code == 400
    assert r.headers["Content-Type"].startswith("text/plain")
    assert r.text == "Bad Request"
    assert "Location" not in r.headers


class TestCreateSessionStrictJson:
    def test_create_session_malformed_json_400(self, main_server):
        r = _post_raw(main_server, b'{"quantity": 2')
        record("test_create_session_malformed_json_400", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text)
        _assert_bad_request(r)

    def test_create_session_json_number_400(self, main_server):
        r = _post_raw(main_server, b"3")
        record("test_create_session_json_number_400", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text)
        _assert_bad_request(r)

    def test_create_session_json_string_400(self, main_server):
        r = _post_raw(main_server, b'"3"')
        record("test_create_session_json_string_400", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text)
        _assert_bad_request(r)

    def test_create_session_json_null_400(self, main_server):
        r = _post_raw(main_server, b"null")
        record("test_create_session_json_null_400", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text)
        _assert_bad_request(r)

    def test_create_session_empty_json_body_502(self, main_server):
        """Empty body with JSON content type is {} -> no quantity -> Stripe rejects -> 502 {error}."""
        r = _post_raw(main_server, b"")
        record("test_create_session_empty_json_body_502", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.headers["Content-Type"].startswith("application/json")
        assert isinstance(r.json().get("error"), str) and r.json()["error"]

    def test_create_session_json_array_502(self, main_server):
        """A JSON array passes strict parsing but has no quantity -> Stripe rejects -> 502 {error}."""
        r = _post_raw(main_server, b"[]")
        record("test_create_session_json_array_502", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.json().get("error")
