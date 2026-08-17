"""Wake-on-LAN: the standalone helper the button and media_player both call."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eversolo import wake_on_lan
from custom_components.eversolo.const import WAKE_ON_LAN_PORTS, WAKE_ON_LAN_RETRY_DELAY

MAC = "aa:bb:cc:00:00:01"


@pytest.mark.parametrize(
    ("host", "broadcast"),
    [
        ("192.168.0.63", "192.168.0.255"),
        ("10.0.0.42", "10.0.0.255"),
    ],
)
def test_subnet_broadcast_derives_the_24_from_an_ipv4_host(
    host: str, broadcast: str
) -> None:
    """F5 woke the unit on its subnet broadcast, not the global one."""
    assert wake_on_lan._subnet_broadcast(host) == broadcast


def test_subnet_broadcast_falls_back_to_global_for_a_hostname() -> None:
    """A host with no /24 to derive falls back to the vendor app's address."""
    assert wake_on_lan._subnet_broadcast("eversolo.local") == "255.255.255.255"


async def test_async_wake_sends_a_magic_packet_to_every_port_twice(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ports, the subnet broadcast, the given MAC — sent twice, nothing else.

    A single send can silently miss the unit's settling window after
    power-off (RESEARCH.md, "A single WoL magic packet can silently fail; the
    integration doesn't retry"), so this sends once immediately and once more
    after the retry delay.
    """
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        wake_on_lan.wakeonlan,
        "send_magic_packet",
        lambda mac, *, ip_address, port: calls.append((mac, ip_address, port)),
    )
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(wake_on_lan.asyncio, "sleep", _fake_sleep)

    await wake_on_lan.async_wake(hass, "192.168.0.63", MAC)

    one_round = [(MAC, "192.168.0.255", port) for port in WAKE_ON_LAN_PORTS]
    assert calls == one_round + one_round
    assert sleeps == [WAKE_ON_LAN_RETRY_DELAY]
