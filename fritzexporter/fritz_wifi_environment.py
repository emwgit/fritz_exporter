"""WiFi airtime and neighbouring networks from the Fritz!Box web interface.

The page ``data.lua?page=chan`` (the channel overview of the WiFi settings) holds,
per band (``24ghz``, ``5ghz``, ``6ghz``), the airtime used in the last hour and the
result of the last scan for neighbouring networks. It answers only for
``xhrId=environment``. The generic web login and the ``data.lua`` fetching are in
:mod:`fritzexporter.fritz_webui`; this module holds the page name and the parser.

``airtimedata`` is ``<time>,<step>,<count>,<a>:<b>,<a>:<b>,...``: one sample per
second, newest first (the same list shifted by the seconds between two requests),
each ``a:b`` in percent with ``a`` never below ``b`` in 10800 samples seen on a
FRITZ!Box 5690 Pro. The box does not document what they are; the munin plugin
``fritzbox_wifi_load`` calls ``a`` received and ``b`` sent airtime.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Final, TypedDict

from fritzexporter.reply import DataError, Issues, Reply, to_float, to_int

__all__ = [
    "WIFI_ENVIRONMENT_PAGE",
    "WIFI_ENVIRONMENT_PARAMETERS",
    "WifiBand",
    "parse_wifi_environment_response",
]

#: The ``data.lua`` page of the channel overview, and the ``xhrId`` it answers to.
WIFI_ENVIRONMENT_PAGE: Final[str] = "chan"
WIFI_ENVIRONMENT_PARAMETERS: Final[dict[str, str]] = {"xhrId": "environment"}

# Names as in the ``wifi_name`` label of the WLANConfiguration metrics.
_BAND_NAMES: Final[dict[str, str]] = {"24ghz": "2.4GHz", "5ghz": "5GHz", "6ghz": "6GHz"}

# The newest samples averaged: one second is too noisy for a scrape every minute.
_AIRTIME_SAMPLES: Final[int] = 60
_AIRTIME_HEADER_FIELDS: Final[int] = 3
_PAIR_FIELDS: Final[int] = 2


class WifiBand(TypedDict):
    """One band: airtime as a ratio of 0 to 1, neighbouring networks by channel."""

    name: str
    airtime_receive: float | None
    airtime_transmit: float | None
    neighbours_same_channel: int | None
    neighbours_other_channels: int | None


def _airtime(text: str, what: str) -> tuple[float, float] | None:
    """The mean of the newest samples as ``(a, b)`` ratios; ``None`` without samples."""
    fields = text.split(",")[_AIRTIME_HEADER_FIELDS:][:_AIRTIME_SAMPLES]
    pairs: list[tuple[float, float]] = []
    for position, field in enumerate(fields):
        parts = field.split(":")
        if len(parts) != _PAIR_FIELDS:
            msg = f"{what} sample {position}: expected <a>:<b>, got {field!r}"
            raise DataError(msg)
        first = to_float(parts[0], f"{what} sample {position}")
        second = to_float(parts[1], f"{what} sample {position}")
        if first is None or second is None:
            msg = f"{what} sample {position}: expected <a>:<b>, got {field!r}"
            raise DataError(msg)
        pairs.append((first, second))
    if not pairs:
        return None
    return (
        sum(a for a, _ in pairs) / len(pairs) / 100,
        sum(b for _, b in pairs) / len(pairs) / 100,
    )


def _neighbours(data: Reply, issues: Issues) -> dict[str, list[int | None]]:
    """The channels of the networks of others, by band id (``isEnvNet``: not our own)."""
    channels: dict[str, list[int | None]] = defaultdict(list)
    for index, entry in issues.objects(data, "scanlist"):
        with issues.item(f"scanlist[{index}]"):
            if entry.opt_flag("isEnvNet"):
                channels[entry.text("bandId")].append(entry.opt_integer("channel"))
    return channels


def parse_wifi_environment_response(raw: object, issues: Issues) -> list[WifiBand]:
    """Extract airtime and neighbouring networks of the bands the box lists.

    A band that is not active is left out. A defective value gives ``None`` and
    a defective item is skipped, both recorded in ``issues``; a reply of the
    wrong shape, or without ``data``, raises :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "chan").require_key("data").opt_obj("data") or Reply({}, "chan data")
    has_airtime = data.opt_flag("hasAirtime") is not False
    neighbours = issues.value(lambda: _neighbours(data, issues)) or {}
    bands: list[WifiBand] = []
    for band_id in data.items("bands"):
        band = data.opt_obj(str(band_id))
        if band is None or band.opt_flag("active") is False:
            continue
        with issues.item(f"band {band_id}"):
            used = [
                to_int(channel, f"chan {band_id} usedChannels")
                for channel in band.items("usedChannels")
            ]
            others = [c for c in neighbours.get(str(band_id), []) if c is not None]
            airtime = None
            text = band.opt_text("airtimedata")
            if has_airtime and text:
                airtime = issues.value(
                    lambda text=text, band_id=band_id: _airtime(text, f"chan {band_id} airtimedata")
                )
            bands.append(
                {
                    "name": _BAND_NAMES.get(str(band_id), str(band_id)),
                    "airtime_receive": None if airtime is None else airtime[0],
                    "airtime_transmit": None if airtime is None else airtime[1],
                    "neighbours_same_channel": sum(1 for c in others if c in used),
                    "neighbours_other_channels": sum(1 for c in others if c not in used),
                }
            )
    return bands
