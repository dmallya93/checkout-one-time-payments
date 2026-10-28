"""Bind address: FLASK_DEBUG=1 -> 127.0.0.1 only; waitress (default) -> 0.0.0.0."""

import socket

import requests

from conftest import record


def _external_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


def _reachable(host: str, port: int) -> bool:
    try:
        return requests.get(f"http://{host}:{port}/health", timeout=10).status_code == 200
    except requests.RequestException:
        return False


class TestBindAddress:
    def test_debug_mode_binds_loopback_only(self, spawn_server):
        ext = _external_ip()
        assert ext, "no non-loopback IPv4 address available"
        srv = spawn_server(FLASK_DEBUG="1")
        ready = srv.wait_ready()
        loop_ok = _reachable("127.0.0.1", srv.port) if ready else False
        ext_ok = _reachable(ext, srv.port) if ready else None
        out = srv.output()
        record("test_debug_mode_binds_loopback_only", exit_code=0 if ready else srv.proc.returncode,
               stdout=out, loopback_reachable=loop_ok, external_reachable=ext_ok, external_ip=ext)
        assert ready, out
        assert f"Python server listening on port {srv.port}!" in out
        assert out.count(f"Python server listening on port {srv.port}!") == 1
        assert loop_ok
        assert ext_ok is False

    def test_waitress_binds_all_interfaces(self, spawn_server):
        ext = _external_ip()
        assert ext, "no non-loopback IPv4 address available"
        srv = spawn_server()
        ready = srv.wait_ready()
        ext_ok = _reachable(ext, srv.port) if ready else False
        out = srv.output()
        record("test_waitress_binds_all_interfaces", exit_code=0 if ready else srv.proc.returncode,
               stdout=out, external_reachable=ext_ok, external_ip=ext)
        assert ready, out
        assert f"Python server listening on port {srv.port}!" in out
        assert ext_ok
