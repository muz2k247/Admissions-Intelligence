"""Tests for scraper/tls.py — AIA (Authority Information Access) TLS chain
completion.

All network access (DNS/socket/HTTP) is mocked — no test in this file may
hit a live host (CLAUDE.md hard rule / QA policy).
"""
from __future__ import annotations

import datetime
import socket
import ssl
from pathlib import Path

import pytest
import requests

from scraper import tls


# ---------------------------------------------------------------------------
# Helpers: build minimal self-signed certs with the `cryptography` library
# ---------------------------------------------------------------------------

def _make_cert(aia_url: str | None = None, common_name: str = "test"):
    """Build a minimal self-signed certificate (issuer == subject),
    optionally carrying an AuthorityInformationAccess (CA Issuers)
    extension. Returns the x509.Certificate object."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, AuthorityInformationAccessOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
    )
    if aia_url:
        builder = builder.add_extension(
            x509.AuthorityInformationAccess(
                [
                    x509.AccessDescription(
                        AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier(aia_url),
                    )
                ]
            ),
            critical=False,
        )
    return builder.sign(key, hashes.SHA256())


def _make_non_self_issued_cert(subject_cn: str, issuer_cn: str, aia_url: str | None = None):
    """Build a cert whose issuer name differs from its subject name (NOT
    self-issued), so `build_completed_bundle`'s "stop once we reach a root"
    check (issuer == subject) never fires. Used to construct a pathological
    looping AIA chain that only the hop cap can terminate. The signature
    isn't cryptographically valid against the stated issuer, but
    build_completed_bundle never verifies it -- it only inspects extensions
    and issuer/subject name equality."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, AuthorityInformationAccessOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
    )
    if aia_url:
        builder = builder.add_extension(
            x509.AuthorityInformationAccess(
                [
                    x509.AccessDescription(
                        AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier(aia_url),
                    )
                ]
            ),
            critical=False,
        )
    return builder.sign(key, hashes.SHA256())


def _der(cert) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding
    return cert.public_bytes(Encoding.DER)


def _pem(cert) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding
    return cert.public_bytes(Encoding.PEM)


@pytest.fixture(autouse=True)
def _clear_bundle_cache():
    """Every build_completed_bundle test wants a clean cache, and must clean
    up any temp files it creates (both from the test itself and from
    tls._BUNDLE_CACHE, since the module cache persists across tests)."""
    tls._BUNDLE_CACHE.clear()
    yield
    for path in list(tls._BUNDLE_CACHE.values()):
        try:
            Path(path).unlink()
        except OSError:
            pass
    tls._BUNDLE_CACHE.clear()


# ---------------------------------------------------------------------------
# _aia_url_is_safe
# ---------------------------------------------------------------------------

class TestAiaUrlIsSafe:
    def test_rejects_non_http_scheme(self, monkeypatch):
        # Should reject before even resolving DNS.
        assert tls._aia_url_is_safe("ftp://example.com/ca.der") is False
        assert tls._aia_url_is_safe("file:///etc/passwd") is False

    def test_rejects_url_with_no_hostname(self):
        assert tls._aia_url_is_safe("http:///ca.der") is False

    def test_rejects_dns_resolution_failure(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            raise OSError("name resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://nonexistent.example/ca.der") is False

    def test_accepts_public_looking_host(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://ca.example.com/ca.der") is True

    def test_rejects_loopback_ip(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://sneaky.example.com/ca.der") is False

    def test_rejects_private_ip(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://internal.example.com/ca.der") is False

    def test_rejects_link_local_ip(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        # cloud metadata endpoint — must be blocked
        assert tls._aia_url_is_safe("http://metadata.example.com/ca.der") is False

    def test_rejects_multicast_ip(self, monkeypatch):
        def fake_getaddrinfo(host, port, proto=None):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("224.0.0.1", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://multicast.example.com/ca.der") is False

    def test_rejects_if_any_resolved_address_is_unsafe(self, monkeypatch):
        # Multiple A/AAAA records — even one private address should be
        # enough to refuse the whole URL (defense in depth).
        def fake_getaddrinfo(host, port, proto=None):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert tls._aia_url_is_safe("http://mixed.example.com/ca.der") is False


# ---------------------------------------------------------------------------
# _load_cert
# ---------------------------------------------------------------------------

class TestLoadCert:
    def test_parses_der_bytes(self):
        cert = _make_cert()
        loaded = tls._load_cert(_der(cert))
        assert loaded.subject == cert.subject

    def test_parses_pem_bytes_as_fallback(self):
        cert = _make_cert()
        loaded = tls._load_cert(_pem(cert))
        assert loaded.subject == cert.subject

    def test_garbage_bytes_raise(self):
        with pytest.raises(ValueError):
            tls._load_cert(b"this is not a certificate at all")


# ---------------------------------------------------------------------------
# _ca_issuer_url
# ---------------------------------------------------------------------------

class TestCaIssuerUrl:
    def test_extracts_url_when_aia_present(self):
        cert = _make_cert(aia_url="http://ca.example.com/intermediate.der")
        assert tls._ca_issuer_url(cert) == "http://ca.example.com/intermediate.der"

    def test_returns_none_when_extension_absent(self):
        cert = _make_cert(aia_url=None)
        assert tls._ca_issuer_url(cert) is None

    def test_returns_none_when_aia_has_no_ca_issuers_entry(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, AuthorityInformationAccessOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.AuthorityInformationAccess(
                    [
                        x509.AccessDescription(
                            AuthorityInformationAccessOID.OCSP,
                            x509.UniformResourceIdentifier("http://ocsp.example.com"),
                        )
                    ]
                ),
                critical=False,
            )
        )
        cert = builder.sign(key, hashes.SHA256())
        assert tls._ca_issuer_url(cert) is None


# ---------------------------------------------------------------------------
# build_completed_bundle
# ---------------------------------------------------------------------------

class FakeAiaResponse:
    """Minimal stand-in for requests.Response used with stream=True + .raw.read()."""

    def __init__(self, body: bytes, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.raw = _FakeRaw(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _FakeRaw:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n, decode_content=False):
        return self._body[:n]


class FakeAiaSession:
    """Stand-in for requests.Session.get used only for AIA fetches."""

    def __init__(self, responses):
        # responses: dict url -> FakeAiaResponse/Exception, or a callable
        self._responses = responses
        self.calls = []
        self.call_kwargs = []

    def get(self, url, timeout=None, stream=None, **kwargs):
        self.calls.append(url)
        self.call_kwargs.append(kwargs)
        if callable(self._responses):
            return self._responses(url)
        if url not in self._responses:
            raise requests.ConnectionError(f"unexpected AIA URL in test: {url}")
        result = self._responses[url]
        if isinstance(result, Exception):
            raise result
        return result


class TestBuildCompletedBundle:
    def test_returns_none_when_leaf_cert_fetch_fails(self, monkeypatch):
        def fake_leaf(host, port, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(tls, "_leaf_cert_der", fake_leaf)
        session = FakeAiaSession({})

        result = tls.build_completed_bundle("giki.edu.pk", session)
        assert result is None

    def test_returns_none_when_leaf_cert_fetch_raises_ssl_error(self, monkeypatch):
        def fake_leaf(host, port, timeout):
            raise ssl.SSLError("handshake failure")

        monkeypatch.setattr(tls, "_leaf_cert_der", fake_leaf)
        session = FakeAiaSession({})

        result = tls.build_completed_bundle("giki.edu.pk", session)
        assert result is None

    def test_returns_none_when_no_aia_extension(self, monkeypatch):
        leaf = _make_cert(aia_url=None)

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(leaf))
        session = FakeAiaSession({})

        result = tls.build_completed_bundle("no-aia.example.com", session)
        assert result is None
        assert session.calls == []  # never attempted an AIA fetch

    def test_successful_bundle_contains_certifi_roots_and_intermediate(self, monkeypatch, tmp_path):
        import certifi

        intermediate = _make_cert(common_name="intermediate-ca")
        leaf = _make_cert(aia_url="http://ca.example.com/intermediate.der")

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(leaf))
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        session = FakeAiaSession(
            {"http://ca.example.com/intermediate.der": FakeAiaResponse(_der(intermediate))}
        )

        result = tls.build_completed_bundle("giki.edu.pk", session)
        try:
            assert result is not None
            assert Path(result).exists()
            content = Path(result).read_bytes()
            certifi_bytes = Path(certifi.where()).read_bytes()
            assert certifi_bytes in content
            assert _pem(intermediate) in content
        finally:
            if result:
                Path(result).unlink(missing_ok=True)
                tls._BUNDLE_CACHE.clear()

    def test_stops_after_max_aia_hops_on_looping_chain(self, monkeypatch):
        # Two certs, neither self-issued (distinct subject/issuer names),
        # whose AIA URLs point at each other -- a pathological loop that
        # must not run forever. Using distinct names is important: certs
        # built as self-issued (issuer == subject) would trip the "stop at
        # a root" check after a single hop, masking whether the hop cap
        # actually works.
        cert_a = _make_non_self_issued_cert("leaf", "issuer-a", aia_url="http://ca.example.com/b.der")
        cert_b = _make_non_self_issued_cert("issuer-a", "issuer-b", aia_url="http://ca.example.com/a.der")

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(cert_a))
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        session = FakeAiaSession(
            {
                "http://ca.example.com/b.der": FakeAiaResponse(_der(cert_b)),
                "http://ca.example.com/a.der": FakeAiaResponse(_der(cert_a)),
            }
        )

        result = tls.build_completed_bundle("looping.example.com", session)
        try:
            # Loop must terminate — total AIA fetch attempts capped at _MAX_AIA_HOPS.
            assert len(session.calls) <= tls._MAX_AIA_HOPS
            assert len(session.calls) == tls._MAX_AIA_HOPS, (
                "chain never reaches a self-issued root, so it should run "
                "the full hop budget, not stop early"
            )
            # A bundle should still be produced since intermediates were collected.
            assert result is not None
        finally:
            if result:
                Path(result).unlink(missing_ok=True)
                tls._BUNDLE_CACHE.clear()

    def test_refuses_oversized_aia_response(self, monkeypatch):
        leaf = _make_cert(aia_url="http://ca.example.com/huge.der")

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(leaf))
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        oversized_body = b"\x00" * (tls._MAX_CERT_BYTES + 100)
        session = FakeAiaSession(
            {"http://ca.example.com/huge.der": FakeAiaResponse(oversized_body)}
        )

        result = tls.build_completed_bundle("huge.example.com", session)
        # No usable intermediate was collected (oversized -> refused -> break).
        assert result is None

    def test_cache_prevents_refetch_for_same_host_and_port(self, monkeypatch):
        intermediate = _make_cert(common_name="intermediate-ca")
        leaf = _make_cert(aia_url="http://ca.example.com/intermediate.der")

        leaf_calls = {"n": 0}

        def fake_leaf(host, port, timeout):
            leaf_calls["n"] += 1
            return _der(leaf)

        monkeypatch.setattr(tls, "_leaf_cert_der", fake_leaf)
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        session = FakeAiaSession(
            {"http://ca.example.com/intermediate.der": FakeAiaResponse(_der(intermediate))}
        )

        result1 = tls.build_completed_bundle("cached.example.com", session)
        result2 = tls.build_completed_bundle("cached.example.com", session)

        try:
            assert result1 == result2
            assert leaf_calls["n"] == 1  # second call served from cache
            assert len(session.calls) == 1  # AIA fetched only once
        finally:
            if result1:
                Path(result1).unlink(missing_ok=True)
                tls._BUNDLE_CACHE.clear()

    def test_different_ports_do_not_share_cache_entry(self, monkeypatch):
        intermediate = _make_cert(common_name="intermediate-ca")
        leaf = _make_cert(aia_url="http://ca.example.com/intermediate.der")

        leaf_calls = {"n": 0}

        def fake_leaf(host, port, timeout):
            leaf_calls["n"] += 1
            return _der(leaf)

        monkeypatch.setattr(tls, "_leaf_cert_der", fake_leaf)
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        session = FakeAiaSession(
            {"http://ca.example.com/intermediate.der": FakeAiaResponse(_der(intermediate))}
        )

        result_443 = tls.build_completed_bundle("multi-port.example.com", session, port=443)
        result_8443 = tls.build_completed_bundle("multi-port.example.com", session, port=8443)

        try:
            # Regression guard: cache key must include port, so a different
            # port for the same host triggers its own leaf-cert fetch and
            # its own bundle, not a stale/shared one.
            assert leaf_calls["n"] == 2
            assert result_443 != result_8443
            assert "multi-port.example.com:443" in tls._BUNDLE_CACHE
            assert "multi-port.example.com:8443" in tls._BUNDLE_CACHE
        finally:
            for path in (result_443, result_8443):
                if path:
                    Path(path).unlink(missing_ok=True)
            tls._BUNDLE_CACHE.clear()

    def test_aia_fetch_disables_redirects(self, monkeypatch):
        # The AIA URL is unverified, attacker-reachable input -- following a
        # redirect would mean fetching a second, never-safety-checked host.
        intermediate = _make_cert(common_name="intermediate-ca")
        leaf = _make_cert(aia_url="http://ca.example.com/intermediate.der")

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(leaf))
        monkeypatch.setattr(
            tls,
            "_resolve_safe_addrinfo",
            lambda url: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        )

        session = FakeAiaSession(
            {"http://ca.example.com/intermediate.der": FakeAiaResponse(_der(intermediate))}
        )

        result = tls.build_completed_bundle("redirect-check.example.com", session)
        try:
            assert len(session.call_kwargs) == 1
            assert session.call_kwargs[0].get("allow_redirects") is False
        finally:
            if result:
                Path(result).unlink(missing_ok=True)
                tls._BUNDLE_CACHE.clear()

    def test_aia_fetch_pins_resolution_to_the_validated_address(self, monkeypatch):
        # Closes the DNS-rebinding gap: whatever address the safety check
        # validated is the exact (and only) address the actual fetch can
        # resolve to, even if a real DNS answer changed in between.
        intermediate = _make_cert(common_name="intermediate-ca")
        leaf = _make_cert(aia_url="http://ca.example.com/intermediate.der")
        pinned_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

        monkeypatch.setattr(tls, "_leaf_cert_der", lambda host, port, timeout: _der(leaf))
        monkeypatch.setattr(tls, "_resolve_safe_addrinfo", lambda url: pinned_infos)

        seen_during_fetch = {}

        def fake_get(url, timeout=None, stream=None, **kwargs):
            # Capture what socket.getaddrinfo resolves to *while the request
            # is in flight* -- this is what a real connection would use.
            seen_during_fetch["infos"] = socket.getaddrinfo("attacker-controlled.example", 80)
            return FakeAiaResponse(_der(intermediate))

        session = FakeAiaSession({})
        session.get = fake_get

        original_getaddrinfo = socket.getaddrinfo
        result = tls.build_completed_bundle("pin-check.example.com", session)
        try:
            assert seen_during_fetch["infos"] == pinned_infos
            # Pinning must not leak past the request it was scoped to.
            assert socket.getaddrinfo is original_getaddrinfo
        finally:
            if result:
                Path(result).unlink(missing_ok=True)
                tls._BUNDLE_CACHE.clear()


class TestPinResolution:
    def test_pins_getaddrinfo_within_block_and_restores_after(self):
        original = socket.getaddrinfo
        fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]

        with tls._pin_resolution(fake_infos):
            assert socket.getaddrinfo("anything.example", 443) == fake_infos
            assert socket.getaddrinfo is not original

        assert socket.getaddrinfo is original

    def test_restores_original_even_if_block_raises(self):
        original = socket.getaddrinfo
        fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]

        with pytest.raises(ValueError):
            with tls._pin_resolution(fake_infos):
                raise ValueError("boom")

        assert socket.getaddrinfo is original
