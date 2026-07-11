"""AIA-based TLS chain completion.

Some institution servers (confirmed: GIKI) are misconfigured to send only
their leaf certificate without the intermediate CA that links it to a trusted
root. Browsers silently recover by following the leaf's Authority Information
Access (AIA) "caIssuers" URL to fetch the missing intermediate; Python's ssl
stack does not. This module reproduces that recovery so we can still verify
such sites against a real trusted root — NOT by disabling verification (which
would accept a tampered response), but by completing the chain the server
should have sent and validating it normally.
"""
from __future__ import annotations

import atexit
import ipaddress
import socket
import ssl
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import requests
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

# host+port -> completed CA bundle path, so we build each host's bundle once.
_BUNDLE_CACHE: dict[str, str] = {}
_MAX_AIA_HOPS = 4  # guard against a pathological / looping AIA chain
_MAX_CERT_BYTES = 1 << 20  # a CA cert is a few KB; cap the AIA download hard


@atexit.register
def _cleanup_bundles() -> None:
    """Remove the temp CA bundles we created (delete=False keeps them alive
    for the process lifetime; without this they'd accumulate in the temp dir)."""
    for path in _BUNDLE_CACHE.values():
        try:
            Path(path).unlink()
        except OSError:
            pass


def _resolve_safe_addrinfo(url: str) -> list | None:
    """Resolve the AIA URL's host and, if every resolved address is public
    (not private/loopback/link-local/reserved/multicast — blocks SSRF at
    internal services or cloud metadata endpoints like 169.254.169.254),
    return the raw getaddrinfo results. Returns None if the URL is
    unsupported, unresolvable, or resolves to any unsafe address.

    The AIA URL comes from an UNVERIFIED leaf cert, so treat it as hostile
    input. The caller pins the actual connection to exactly the addresses
    returned here (see `_pin_resolution`), so there's no gap between this
    check and the real connection for a DNS answer to change in (DNS
    rebinding). The completed chain still has to validate against a real
    root regardless — this only stops the outbound side-effect of fetching
    an attacker-chosen URL."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return None
    return infos


def _aia_url_is_safe(url: str) -> bool:
    """Boolean convenience wrapper around `_resolve_safe_addrinfo`."""
    return _resolve_safe_addrinfo(url) is not None


@contextmanager
def _pin_resolution(infos: list):
    """Force socket.getaddrinfo to return exactly `infos` (an already
    safety-checked result) for the duration of the wrapped request, so the
    connection actually made can't resolve to a different, e.g. DNS-rebound,
    address than the one just validated. Not thread-safe — scoped as tightly
    as possible around a single outbound request and always restored."""
    original = socket.getaddrinfo

    def _pinned(*args, **kwargs):
        return infos

    socket.getaddrinfo = _pinned
    try:
        yield
    finally:
        socket.getaddrinfo = original


def _load_cert(raw: bytes) -> x509.Certificate:
    """AIA caIssuers responses are usually DER, but some CAs serve PEM — try
    both before giving up."""
    try:
        return x509.load_der_x509_certificate(raw)
    except ValueError:
        return x509.load_pem_x509_certificate(raw)


def _leaf_cert_der(host: str, port: int, timeout: int) -> bytes:
    ctx = ssl.create_default_context()  # only to read the cert, not to trust it
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            return ss.getpeercert(binary_form=True)


def _ca_issuer_url(cert: x509.Certificate) -> str | None:
    try:
        aia = cert.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        ).value
    except x509.ExtensionNotFound:
        return None
    for desc in aia:
        if desc.access_method == x509.oid.AuthorityInformationAccessOID.CA_ISSUERS:
            return desc.access_location.value
    return None


def build_completed_bundle(
    host: str, session: requests.Session, port: int = 443, timeout: int = 30
) -> str | None:
    """Return a path to a CA bundle (certifi roots + fetched intermediates)
    that completes `host`'s chain, or None if recovery isn't possible.

    The returned bundle still roots in certifi's trusted store, so verifying
    against it is a real trust decision — we've only supplied the intermediate
    the server omitted, exactly what a browser's AIA fetch does.
    """
    cache_key = f"{host}:{port}"
    if cache_key in _BUNDLE_CACHE:
        return _BUNDLE_CACHE[cache_key]

    try:
        der = _leaf_cert_der(host, port, timeout)
        cert = x509.load_der_x509_certificate(der)
    except (OSError, ssl.SSLError, ValueError):
        return None

    intermediates: list[bytes] = []
    for _ in range(_MAX_AIA_HOPS):
        url = _ca_issuer_url(cert)
        if not url:
            break
        addrinfo = _resolve_safe_addrinfo(url)
        if addrinfo is None:
            break
        try:
            # Redirects are disabled: the AIA URL is unverified attacker-
            # reachable input, and following a redirect would re-resolve
            # DNS for a second, unchecked host.
            with _pin_resolution(addrinfo):
                resp = session.get(url, timeout=timeout, stream=True, allow_redirects=False)
            resp.raise_for_status()
            raw = resp.raw.read(_MAX_CERT_BYTES + 1, decode_content=True)
            if len(raw) > _MAX_CERT_BYTES:
                break  # implausibly large for a CA cert — refuse it
            cert = _load_cert(raw)
        except (requests.RequestException, ValueError, OSError):
            break
        intermediates.append(cert.public_bytes(Encoding.PEM))
        # Stop once we reach a self-issued cert (a root) — nothing left to fetch.
        if cert.issuer == cert.subject:
            break

    if not intermediates:
        return None

    bundle = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pem", prefix=f"aia_{host}_"
    )
    try:
        bundle.write(Path(certifi.where()).read_bytes())
        for pem in intermediates:
            bundle.write(b"\n")
            bundle.write(pem)
    except OSError:
        bundle.close()
        Path(bundle.name).unlink(missing_ok=True)
        return None
    bundle.close()

    _BUNDLE_CACHE[cache_key] = bundle.name
    return bundle.name
