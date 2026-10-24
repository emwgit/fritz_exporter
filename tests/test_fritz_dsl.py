"""Tests for the DSL error counters (fritz_dsl.py)."""

import copy
from typing import Any

import pytest

from fritzexporter.fritz_dsl import parse_dsl_stat_response
from fritzexporter.reply import DataError, Issues

NONE = {"receive": None, "transmit": None}


def _row(title: str, us: str, ds: str) -> dict[str, Any]:
    return {"title": title, "val": [{"us": us, "ds": ds}]}


# errorCounters of a FRITZ!Box 5690 Pro on VDSL (Fritz!OS 8.x, German interface).
ERROR_COUNTERS: list[dict[str, Any]] = [
    {"title": "Sekunden mit"},
    _row("Fehlern (ES)", "0", "14"),
    _row("Fehlern (SES)", "0", "1"),
    {"title": ""},
    {"title": "Nicht behebbare Fehler (CRC)"},
    _row("pro Minute", "0", "0"),
    _row("letzte 15 Minuten", "2", "3"),
    {"title": ""},
    {"title": "korrigierte DTU"},
    _row("pro Minute", "0.2", "1.8"),
    _row("letzte 15 Minuten", "9", "25"),
    {"title": ""},
    {"title": "unkorrigierte DTU"},
    _row("pro Minute", "0", "0.5"),
    _row("letzte 15 Minuten", "0", "4"),
]


def page(rows: list | None = None) -> dict:
    return {
        "pid": "dslStat",
        "data": {"isConnected": True, "errorCounters": rows or ERROR_COUNTERS},
    }


class TestParseDslStat:
    def test_reads_both_directions_of_every_counter(self):
        issues = Issues()

        counters = parse_dsl_stat_response(page(), issues)

        assert counters == {
            "errored_seconds": {"receive": 14.0, "transmit": 0.0},
            "severely_errored_seconds": {"receive": 1.0, "transmit": 0.0},
            "crc_errors_minute": {"receive": 0.0, "transmit": 0.0},
            "crc_errors_15_minutes": {"receive": 3.0, "transmit": 2.0},
            "corrected_dtus_minute": {"receive": 1.8, "transmit": 0.2},
            "corrected_dtus_15_minutes": {"receive": 25.0, "transmit": 9.0},
            "uncorrected_dtus_minute": {"receive": 0.5, "transmit": 0.0},
            "uncorrected_dtus_15_minutes": {"receive": 4.0, "transmit": 0.0},
        }
        assert issues.messages == []

    def test_titles_are_not_used(self):
        # The box answers in its interface language; only the structure counts.
        english = copy.deepcopy(ERROR_COUNTERS)
        for row in english:
            if row["title"]:
                row["title"] = "translated"

        assert parse_dsl_stat_response(page(english), Issues()) == parse_dsl_stat_response(
            page(), Issues()
        )

    @pytest.mark.parametrize("raw", [{"data": {"isConnected": False}}, {"data": []}])
    def test_line_without_counters_gives_none_and_no_issue(self, raw):
        issues = Issues()

        counters = parse_dsl_stat_response(raw, issues)

        assert all(v == NONE for v in counters.values())
        assert issues.messages == []

    def test_missing_section_gives_none_and_is_recorded(self):
        issues = Issues()

        counters = parse_dsl_stat_response(page(ERROR_COUNTERS[:12]), issues)

        assert counters["corrected_dtus_15_minutes"] == {"receive": 25.0, "transmit": 9.0}
        assert counters["uncorrected_dtus_minute"] == NONE
        assert issues.messages == ["dslStat errorCounters: expected 4 sections, found 3"]

    def test_defective_value_gives_none_and_is_recorded(self):
        rows = copy.deepcopy(ERROR_COUNTERS)
        rows[1]["val"][0]["ds"] = "n/a"
        issues = Issues()

        counters = parse_dsl_stat_response(page(rows), issues)

        assert counters["errored_seconds"] == {"receive": None, "transmit": 0.0}
        assert counters["severely_errored_seconds"] == {"receive": 1.0, "transmit": 0.0}
        assert [m.split(", got")[0] for m in issues.messages] == [
            "dslStat errorCounters section 0 errored_seconds val[0] ds: expected a number"
        ]

    def test_value_of_the_wrong_shape_gives_none_and_is_recorded(self):
        rows = copy.deepcopy(ERROR_COUNTERS)
        rows[1]["val"] = "none"
        issues = Issues()

        counters = parse_dsl_stat_response(page(rows), issues)

        assert counters["errored_seconds"] == NONE
        assert issues.messages == [
            "dslStat errorCounters section 0 errored_seconds val: expected a list, got str"
        ]

    @pytest.mark.parametrize(
        "raw",
        [
            [],
            {"pid": "dslStat"},
            {"data": "not an object"},
            {"data": {"errorCounters": "not a list"}},
            {"data": {"errorCounters": ["not an object"]}},
        ],
    )
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_dsl_stat_response(raw, Issues())
