"""Runtime proof that the shadow decision container has no IP default route."""

from __future__ import annotations

from pathlib import Path


def assert_no_ip_egress(
    ipv4_route: Path = Path("/proc/net/route"),
    ipv6_route: Path = Path("/proc/net/ipv6_route"),
) -> None:
    """Fail closed when a non-loopback IPv4 or IPv6 default route is visible."""

    if not ipv4_route.is_file() or not ipv6_route.is_file():
        raise RuntimeError("cannot prove shadow network isolation from procfs")
    for index, line in enumerate(ipv4_route.read_text(encoding="ascii").splitlines()):
        if index == 0 or not line.strip():
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "00000000" and fields[0] != "lo":
            raise RuntimeError(f"shadow engine has an IPv4 default route on {fields[0]}")
    for line in ipv6_route.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        destination, prefix, interface = fields[0], fields[1], fields[-1]
        if destination == "0" * 32 and prefix == "00000000" and interface != "lo":
            raise RuntimeError(f"shadow engine has an IPv6 default route on {interface}")
