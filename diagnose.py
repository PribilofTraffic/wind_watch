#!/usr/bin/env python3
"""
Failure diagnosis: when a fetch fails, work out whose end the problem is on.

Every failure is sorted into one of four causes:

    local_network  Our own internet or DNS is down. Nothing to fix at the
                   source; nothing can be pushed out either.
    source_down    Our internet works but the source does not answer, or
                   answers with a server error (HTTP 5xx). Their end.
    source_stale   The source answers normally but what it hands out is old
                   (station, sensor or forecast office not updating). Their
                   end. Set by the caller, which knows the data's age.
    our_error      The source answered and we could not use it: the format
                   changed, the URL moved (404), or we were refused or
                   rate-limited (401/403/429). Ours to fix.

Inputs (environment):
    CONNECTIVITY_PROBES  host:port pairs tried with a bare TCP connect (no
                         data is sent) to decide whether the internet is up.
                         Default "1.1.1.1:443,8.8.8.8:53": two unrelated
                         networks, addressed by IP so DNS is not involved.
    DNS_PROBE_HOST       A well-known name used to tell "DNS is broken here"
                         from "only the source's name fails". Default
                         "dns.google".

This module has no dependency on the rest of the service so the other
collectors can reuse it.
"""

import os
import socket
import time
from typing import Optional, Tuple

import requests

LOCAL_NETWORK = 'local_network'
SOURCE_DOWN = 'source_down'
SOURCE_STALE = 'source_stale'
OUR_ERROR = 'our_error'

# Plain-language labels used in logs, reports and alerts.
CAUSE_LABELS = {
    LOCAL_NETWORK: 'our internet or DNS is down (our end)',
    SOURCE_DOWN: 'source not answering (their end)',
    SOURCE_STALE: 'source answering but not updating (their end)',
    OUR_ERROR: 'source answered but we could not use it (ours to fix)',
}

CONNECTIVITY_PROBES = os.getenv('CONNECTIVITY_PROBES', '1.1.1.1:443,8.8.8.8:53')
DNS_PROBE_HOST = os.getenv('DNS_PROBE_HOST', 'dns.google')

_cache = {'at': 0.0, 'value': True}


def internet_ok(max_age: float = 60.0) -> bool:
    """True if at least one probe host accepts a TCP connection.

    The answer is remembered for a minute so one bad cycle with several
    failed requests only probes once.
    """
    if time.time() - _cache['at'] < max_age:
        return _cache['value']
    ok = False
    for probe in CONNECTIVITY_PROBES.split(','):
        host, _, port = probe.strip().rpartition(':')
        if not host:
            continue
        try:
            with socket.create_connection((host, int(port)), timeout=5):
                ok = True
                break
        except (OSError, ValueError):
            continue
    _cache.update(at=time.time(), value=ok)
    return ok


def _dns_works() -> bool:
    try:
        socket.getaddrinfo(DNS_PROBE_HOST, 443)
        return True
    except OSError:
        return False


def _is_dns_failure(exc: BaseException) -> bool:
    """Walk the exception chain looking for a name-resolution error."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, socket.gaierror) or 'NameResolutionError' in type(exc).__name__:
            return True
        text = str(exc)
        if 'Name or service not known' in text or 'Temporary failure in name resolution' in text:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def classify_failure(exc: BaseException) -> Tuple[str, str]:
    """Return (cause, short detail) for an exception raised by a fetch."""
    # The source answered with an HTTP error status.
    response: Optional[requests.Response] = getattr(exc, 'response', None)
    if isinstance(exc, requests.HTTPError) and response is not None:
        code = response.status_code
        if code >= 500:
            return SOURCE_DOWN, f'HTTP {code} from source'
        if code == 429:
            return OUR_ERROR, 'HTTP 429: source is rate-limiting us'
        if code in (401, 403):
            return OUR_ERROR, f'HTTP {code}: source refused us (check User-Agent)'
        if code == 404:
            return OUR_ERROR, 'HTTP 404: the URL we ask for no longer exists'
        return OUR_ERROR, f'HTTP {code} from source'

    # We never got an answer: connection refused, timeout, DNS failure.
    if isinstance(exc, requests.RequestException):
        if not internet_ok():
            return LOCAL_NETWORK, 'no route to the internet from this host'
        if _is_dns_failure(exc):
            if not _dns_works():
                return LOCAL_NETWORK, 'DNS lookups are failing on this host'
            return SOURCE_DOWN, "the source's host name does not resolve"
        if isinstance(exc, requests.Timeout):
            return SOURCE_DOWN, 'source timed out'
        return SOURCE_DOWN, 'could not connect to source'

    # We got an answer but it was not what the code expects.
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError, AttributeError)):
        return OUR_ERROR, f'unexpected response format ({type(exc).__name__}: {exc})'[:200]

    return OUR_ERROR, f'{type(exc).__name__}: {exc}'[:200]


def describe(cause: Optional[str], detail: Optional[str] = None) -> str:
    """Human-readable text for a cause, e.g. for an alert line."""
    label = CAUSE_LABELS.get(cause or '', 'cause unknown')
    return f'{label}: {detail}' if detail else label
