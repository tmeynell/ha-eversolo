"""Wake-on-LAN: the device's only power-on mechanism.

``WakeThread.wakeOnLan(String mac)`` — searched across all 10 DEX of the
control app — is the app's *only* wake path; there is no HTTP boot endpoint
anywhere in it. It broadcasts one magic packet to ``255.255.255.255`` on UDP
9517. #10's F5 also woke the unit in ~15 s on the standard port 9, sent to the
local /24's broadcast address rather than the global one, so both ports are
sent here, to the subnet address (see :data:`.const.WAKE_ON_LAN_PORTS`).

There is no ``homeassistant.components.wake_on_lan.async_send_magic_packet``
to delegate to — that component only registers a ``ServiceCall`` handler, and
does the real work with the **``wakeonlan`` PyPI package**, exactly as
``wakeonlan`` is called directly here, in an executor job since it builds and
sends on a real (blocking) socket.
"""

from __future__ import annotations

import ipaddress

import wakeonlan

from homeassistant.core import HomeAssistant

from .const import WAKE_ON_LAN_PORTS


def _subnet_broadcast(host: str) -> str:
    """Return the host's own /24 broadcast address, or the global one for a name.

    F5 woke the unit on its subnet broadcast address, not the vendor app's
    global one, so that is the address sent here — some networks drop a
    global broadcast that a subnet-directed one still reaches. A host that is
    not a bare IPv4 address — the config flow also accepts a hostname — has no
    subnet to derive one from, so this falls back to the global broadcast the
    vendor's own app uses.
    """
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return "255.255.255.255"
    return str(ipaddress.ip_network(f"{address}/24", strict=False).broadcast_address)


async def async_wake(hass: HomeAssistant, host: str, mac: str) -> None:
    """Broadcast a magic packet for `mac`, on every port the unit answers on."""
    broadcast = _subnet_broadcast(host)

    def _send() -> None:
        for port in WAKE_ON_LAN_PORTS:
            wakeonlan.send_magic_packet(mac, ip_address=broadcast, port=port)

    await hass.async_add_executor_job(_send)
