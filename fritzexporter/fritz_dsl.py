"""DSL error counters from the Fritz!Box web interface.

TR-064 has only the cumulative ``NewFECErrors`` and ``NewCRCErrors``. The web UI
shows more, read from ``data.lua?page=dslStat``: errored and severely errored
seconds, and CRC errors, corrected and uncorrected DTUs as a per-minute value and
a count over the last 15 minutes, each for both directions. The generic web login
and the ``data.lua`` fetching are in :mod:`fritzexporter.fritz_webui`; this module
holds only the page name and the response parser.

The page is localised and its rows carry only a translated ``title``, so the
parser reads ``errorCounters`` by structure: rows without a title separate the
sections, and the rows with a ``val`` in each section are read in order.
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

from fritzexporter.reply import DataError, Issues, Reply

__all__ = [
    "DSL_STAT_PAGE",
    "Directions",
    "DslErrorCounters",
    "parse_dsl_stat_response",
]

#: The ``data.lua`` page that carries the DSL line statistics.
DSL_STAT_PAGE: Final[str] = "dslStat"

# Order of the sections of ``errorCounters`` and of the value rows in each.
_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("errored_seconds", "severely_errored_seconds"),
    ("crc_errors_minute", "crc_errors_15_minutes"),
    ("corrected_dtus_minute", "corrected_dtus_15_minutes"),
    ("uncorrected_dtus_minute", "uncorrected_dtus_15_minutes"),
)


class Directions(TypedDict):
    """One value per direction; ``None`` where the page has none."""

    receive: float | None
    transmit: float | None


class DslErrorCounters(TypedDict):
    """The error counters of the line; the DTUs are G.INP retransmission units."""

    errored_seconds: Directions
    severely_errored_seconds: Directions
    crc_errors_minute: Directions
    crc_errors_15_minutes: Directions
    corrected_dtus_minute: Directions
    corrected_dtus_15_minutes: Directions
    uncorrected_dtus_minute: Directions
    uncorrected_dtus_15_minutes: Directions


def _value_rows(rows: list[Any]) -> list[list[dict[str, Any]]]:
    """Split ``errorCounters`` at the rows without a title; keep the rows with a value."""
    sections: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            msg = f"dslStat errorCounters[{index}]: expected an object, got {type(row).__name__}"
            raise DataError(msg)
        if not row.get("title"):
            if current:
                sections.append(current)
            current = []
        elif row.get("val"):
            current.append(row)
    if current:
        sections.append(current)
    return sections


def _directions(row: dict[str, Any], what: str, issues: Issues) -> Directions:
    """Read ``val[0]`` of a row: ``ds`` is the downstream (receive), ``us`` the upstream."""
    values = row["val"]
    if not isinstance(values, list):
        msg = f"{what} val: expected a list, got {type(values).__name__}"
        issues.messages.append(msg)
        return {"receive": None, "transmit": None}
    reply = issues.value(lambda: Reply(values[0], f"{what} val[0]"))
    if reply is None:
        return {"receive": None, "transmit": None}
    return {
        "receive": issues.opt_num(reply, "ds"),
        "transmit": issues.opt_num(reply, "us"),
    }


def parse_dsl_stat_response(raw: object, issues: Issues) -> DslErrorCounters:
    """Extract the error counters of both directions.

    A missing or defective section gives ``None`` values, a defective one recorded
    in ``issues``. A reply of the wrong shape, or without ``data``, raises
    :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "dslStat").require_key("data").opt_obj("data") or Reply({}, "dslStat data")
    rows = data.items("errorCounters")
    sections = _value_rows(rows)
    if rows and len(sections) != len(_SECTIONS):
        issues.messages.append(
            f"dslStat errorCounters: expected {len(_SECTIONS)} sections, found {len(sections)}"
        )

    def read(position: int, offset: int) -> Directions:
        section = sections[position] if position < len(sections) else []
        if offset >= len(section):
            return {"receive": None, "transmit": None}
        what = f"dslStat errorCounters section {position} {_SECTIONS[position][offset]}"
        return _directions(section[offset], what, issues)

    return {
        "errored_seconds": read(0, 0),
        "severely_errored_seconds": read(0, 1),
        "crc_errors_minute": read(1, 0),
        "crc_errors_15_minutes": read(1, 1),
        "corrected_dtus_minute": read(2, 0),
        "corrected_dtus_15_minutes": read(2, 1),
        "uncorrected_dtus_minute": read(3, 0),
        "uncorrected_dtus_15_minutes": read(3, 1),
    }
