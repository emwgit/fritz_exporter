"""WAN throughput per QoS class from the Fritz!Box web interface.

The page ``data.lua?page=netMoni`` (the online monitor of the internet connection)
holds, per WAN link (``sync_groups``), the throughput of the last samples, split by
traffic class. It answers with the samples only for ``xhrId=updateGraphs``. The
generic web login and the ``data.lua`` fetching are in
:mod:`fritzexporter.fritz_webui`; this module holds the page name and the parser.

The lists are named ``us_<class>_bps_curr`` (upstream: realtime, important, default,
background) and ``ds_bps_curr``, ``ds_mc_bps_curr`` (downstream: internet, multicast).
Despite ``bps`` the values are bytes per second: ``ds_bps_max`` of the page is the
sync rate in ``downstream`` (bit/s) divided by eight, on a FRITZ!Box 5690 Pro. The
samples are newest first, about four seconds apart (the same list shifted by two
samples between two requests eight seconds apart), 20 of them.
"""

from __future__ import annotations

from typing import Final, TypedDict

from fritzexporter.reply import Issues, Reply, to_float

__all__ = [
    "WAN_QOS_PAGE",
    "WAN_QOS_PARAMETERS",
    "GuestThroughput",
    "QosThroughput",
    "WanQos",
    "parse_wan_qos_response",
]

#: The ``data.lua`` page of the online monitor, and the ``xhrId`` it answers to.
WAN_QOS_PAGE: Final[str] = "netMoni"
WAN_QOS_PARAMETERS: Final[dict[str, str]] = {"xhrId": "updateGraphs"}

# The class names are those of the page, not of the munin plugin (high, low, iptv).
_CLASSES: Final[tuple[tuple[str, str, str], ...]] = (
    ("us_realtime_bps_curr", "tx", "realtime"),
    ("us_important_bps_curr", "tx", "important"),
    ("us_default_bps_curr", "tx", "default"),
    ("us_background_bps_curr", "tx", "background"),
    ("ds_bps_curr", "rx", "internet"),
    ("ds_mc_bps_curr", "rx", "multicast"),
)
# The guest network: a list of samples downstream, a list of one value upstream. Whether
# it is part of the classes above or in addition to them is not documented.
_GUESTS: Final[tuple[tuple[str, str], ...]] = (
    ("guest_us_bps", "tx"),
    ("ds_guest_bps_curr", "rx"),
)
_BITS_PER_BYTE: Final[int] = 8


class QosThroughput(TypedDict):
    """The mean throughput of one traffic class on one WAN link, in bit/s."""

    link: str
    direction: str
    qos_class: str
    bits_per_second: float


class GuestThroughput(TypedDict):
    """The mean throughput of the guest network on one WAN link, in bit/s."""

    link: str
    direction: str
    bits_per_second: float


class WanQos(TypedDict):
    """The classes and the guest network of all WAN links."""

    classes: list[QosThroughput]
    guests: list[GuestThroughput]


def _mean_bits(group: Reply, key: str, what: str) -> float | None:
    """The mean of all samples of a list in bit/s; ``None`` if it is absent or empty."""
    samples = [to_float(item, f"{what} {key}") for item in group.items(key)]
    values = [value for value in samples if value is not None]
    if not values:
        return None
    return sum(values) / len(values) * _BITS_PER_BYTE


def parse_wan_qos_response(raw: object, issues: Issues) -> WanQos:
    """Extract the throughput per traffic class and of the guest network of every WAN link.

    A class whose list is absent or empty is left out. A link, or a class with a
    defective value, is skipped and recorded in ``issues``; a reply of the wrong
    shape, or without ``data``, raises :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "netMoni").require_key("data").opt_obj("data")
    result: WanQos = {"classes": [], "guests": []}
    if data is None:
        return result
    for index, group in issues.objects(data, "sync_groups"):
        what = f"netMoni sync_groups[{index}]"
        with issues.item(what):
            # "sync_dsl" is the name of the link on a DSL box; the prefix says nothing.
            link = group.text("name").removeprefix("sync_")
            for key, direction, qos_class in _CLASSES:
                with issues.item(f"{what} {key}"):
                    bits = _mean_bits(group, key, what)
                    if bits is not None:
                        result["classes"].append(
                            {
                                "link": link,
                                "direction": direction,
                                "qos_class": qos_class,
                                "bits_per_second": bits,
                            }
                        )
            for key, direction in _GUESTS:
                with issues.item(f"{what} {key}"):
                    bits = _mean_bits(group, key, what)
                    if bits is not None:
                        result["guests"].append(
                            {"link": link, "direction": direction, "bits_per_second": bits}
                        )
    return result
