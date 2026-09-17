"""Host-only addresses for separately bound and advertised v2 services."""

import ipaddress
import re


def validate_host(value):
    """Accept ASCII DNS names, IPv4, or bare IPv6 (without a zone identifier)."""
    if not isinstance(value, str) or not value or "%" in value:
        raise ValueError("Expected a DNS name, IPv4 address, or bare IPv6 address")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    name = value.removesuffix(".")
    if (
        len(name) <= 253
        and not re.fullmatch(r"[0-9.]+", name)
        and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in name.split("."))
    ):
        return value
    raise ValueError("Expected a DNS name, IPv4 address, or bare IPv6 address; not a URL or host:port")


def url_host(value):
    """Bracket bare IPv6 only when embedding a validated host in a URL."""
    validate_host(value)
    return f"[{value}]" if ":" in value else value
