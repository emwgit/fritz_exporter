"""DSL error counters and line data from the Fritz!Box web interface.

TR-064 has only the cumulative ``NewFECErrors`` and ``NewCRCErrors``. The web UI
shows more, read from ``data.lua?page=dslStat``: errored and severely errored
seconds, and CRC errors, corrected and uncorrected DTUs as a per-minute value and
a count over the last 15 minutes, each for both directions; also the line length,
the profile, the carrier set and the vectoring mode. The generic web login and
the ``data.lua`` fetching are in :mod:`fritzexporter.fritz_webui`; this module
holds only the page name and the response parser.

The page is localised and its rows carry only a translated ``title``, so the
parser reads it by structure: rows without a title separate the sections, and
the rows with a ``val`` in each section are read in order. The line length is the
row with the unit ``m``. Profile, carrier set and vectoring are read only from the
section layout seen on VDSL boxes and are absent otherwise.
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

from fritzexporter.reply import DataError, Issues, Reply, to_float

__all__ = [
    "DSL_STAT_PAGE",
    "Directions",
    "DslErrorCounters",
    "DslLine",
    "DslStatistics",
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
# Rows of the last two sections of ``negotiatedValues`` on VDSL: profile and vectoring, carrier set.
_VDSL_PROFILE_ROWS: Final[tuple[int, int]] = (2, 1)


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


class DslLine(TypedDict):
    """Line data; ``None`` where the page has none or has a layout not seen before."""

    length_meters: float | None
    profile: str | None
    carrier_set: str | None
    vectoring_receive: str | None
    vectoring_transmit: str | None


class DslStatistics(TypedDict):
    errors: DslErrorCounters
    line: DslLine


def _value_rows(rows: list[Any], key: str) -> list[list[dict[str, Any]]]:
    """Split the rows of ``key`` at the rows without a title; keep the rows with a value."""
    sections: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            msg = f"dslStat {key}[{index}]: expected an object, got {type(row).__name__}"
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


def _first_value(row: dict[str, Any]) -> object:
    values = row.get("val")
    return values[0] if isinstance(values, list) and values else None


def _text(row: dict[str, Any]) -> str | None:
    """The text of a row such as ``val: ["35b"]``; ``None`` for any other shape."""
    value = _first_value(row)
    return value if isinstance(value, str) and value else None


def _text_of(row: dict[str, Any], direction: str, issues: Issues) -> str | None:
    """``ds`` or ``us`` of a row such as ``val: [{"us": "full", "ds": "full"}]``."""
    value = _first_value(row)
    if not isinstance(value, dict):
        return None
    what = f"dslStat negotiatedValues vectoring {direction}"
    return issues.value(lambda: Reply(value, what).opt_text(direction))


def _parse_line(data: Reply, issues: Issues) -> DslLine:
    rows = data.items("negotiatedValues")
    sections = _value_rows(rows, "negotiatedValues")
    line: DslLine = {
        "length_meters": None,
        "profile": None,
        "carrier_set": None,
        "vectoring_receive": None,
        "vectoring_transmit": None,
    }
    for index, row in enumerate(rows):
        if row.get("unit") == "m":
            what = f"dslStat negotiatedValues[{index}] line length"
            line["length_meters"] = issues.convert(_first_value(row), to_float, what)
            break
    if tuple(len(section) for section in sections[3:]) == _VDSL_PROFILE_ROWS:
        profile, vectoring = sections[3]
        line["profile"] = _text(profile)
        line["vectoring_receive"] = _text_of(vectoring, "ds", issues)
        line["vectoring_transmit"] = _text_of(vectoring, "us", issues)
        line["carrier_set"] = _text(sections[4][0])
    return line


def parse_dsl_stat_response(raw: object, issues: Issues) -> DslStatistics:
    """Extract the error counters of both directions and the line data.

    A missing or defective section gives ``None`` values, a defective one recorded
    in ``issues``. A reply of the wrong shape, or without ``data``, raises
    :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "dslStat").require_key("data").opt_obj("data") or Reply({}, "dslStat data")
    return {"errors": _parse_error_counters(data, issues), "line": _parse_line(data, issues)}


def _parse_error_counters(data: Reply, issues: Issues) -> DslErrorCounters:
    rows = data.items("errorCounters")
    sections = _value_rows(rows, "errorCounters")
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
