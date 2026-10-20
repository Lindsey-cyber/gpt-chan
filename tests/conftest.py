import pytest

@pytest.fixture(autouse=True)
def isolate_proxy_environment(monkeypatch):
    # Unit tests use mocked clients and must not inherit the executor's SOCKS proxy.
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(name, raising=False)
