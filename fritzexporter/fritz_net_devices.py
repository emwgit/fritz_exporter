"""WiFi link speeds of the network devices from the Fritz!Box web interface.

TR-064 gives one speed per client for the radios of the box itself
(``X_AVM-DE_Speed``). The page ``data.lua?page=netDev``, the network overview of
the web UI, lists every active device including those on mesh repeaters, with a
property text such as ``5 GHz, 1201 / 286 Mbit/s``: the band and the two link speeds.
A client that uses several bands at once (multi-link operation) has one such text
per band. The generic web login and the ``data.lua`` fetching are in
:mod:`fritzexporter.fritz_webui`; this module holds the page name and the parser.

The two speeds are in the order of the web UI. The box does not document which one
is the receiving direction; the munin plugin ``fritzbox_wifi_speeds`` calls the
first one ``rx``.

The text is localised, but the unit and the band are not; only the decimal
separator is (``2,4 GHz``).
"""

from __future__ import annotations

import re
from typing import Final, TypedDict

from fritzexporter.reply import Issues, Reply

__all__ = [
    "NET_DEV_PAGE",
    "WifiLink",
    "parse_net_dev_response",
]

#: The ``data.lua`` page of the network overview.
NET_DEV_PAGE: Final[str] = "netDev"

# "<band> GHz, <speed> / <speed> <unit>bit/s", e.g. "2,4 GHz, 144 / 1 Mbit/s".
_NUMBER = r"\d+(?:[.,]\d+)?"
_LINK = re.compile(
    rf"\b(?P<band>{_NUMBER})\s*GHz,\s*(?P<first>{_NUMBER})\s*/\s*(?P<second>{_NUMBER})"
    r"\s*(?P<unit>[GMk])bit(?:/s)?",
    re.IGNORECASE,
)
_BAND_NAMES: Final[dict[float, str]] = {2.4: "2.4GHz", 5.0: "5GHz", 6.0: "6GHz"}
_UNIT_BITS: Final[dict[str, float]] = {"k": 1e3, "m": 1e6, "g": 1e9}


class WifiLink(TypedDict):
    """One band of one client: the speeds in bit/s."""

    mac: str
    band: str
    receive_bps: float
    transmit_bps: float


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _parse_property(text: str) -> tuple[str, float, float] | None:
    """Return ``(band, first speed, second speed)``, or ``None`` if the text is no WiFi link."""
    match = _LINK.search(text)
    if match is None:
        return None
    band = _BAND_NAMES.get(_number(match["band"]))
    if band is None:
        return None
    scale = _UNIT_BITS[match["unit"].lower()]
    return band, _number(match["first"]) * scale, _number(match["second"]) * scale


def parse_net_dev_response(raw: object, issues: Issues) -> list[WifiLink]:
    """Extract the WiFi links of the active devices.

    A device without a WiFi link text (Ethernet, powerline) gives nothing. A
    device that is not readable is skipped and recorded in ``issues``. A MAC
    address and band that occur twice (a device listed under two names) give one
    entry, the first. A reply of the wrong shape, or without ``data``, raises
    :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "netDev").require_key("data").opt_obj("data")
    links: dict[tuple[str, str], WifiLink] = {}
    if data is None:
        return []
    for index, device in issues.objects(data, "active"):
        what = f"netDev active[{index}]"
        with issues.item(what):
            mac = device.text("mac").upper()
            for position, prop in enumerate(device.items("properties")):
                text = Reply(prop, f"{what} properties[{position}]").text("txt", "")
                link = _parse_property(text)
                if link is None:
                    continue
                band, receive, transmit = link
                links.setdefault(
                    (mac, band),
                    {"mac": mac, "band": band, "receive_bps": receive, "transmit_bps": transmit},
                )
    return list(links.values())
