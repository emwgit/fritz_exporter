"""Tests for the DSL error counters (fritz_dsl.py)."""

import copy
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_dsl import parse_dsl_stat_response
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError, Issues

from .collect import collect_device, services

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


# negotiatedValues of the same box: the values the exporter reads, with the sections of the page.
NEGOTIATED_VALUES: list[dict[str, Any]] = [
    {"unit": "kbit/s", **_row("DSLAM-Datenrate Max.", "42464", "225000")},
    {"unit": "kbit/s", **_row("Leitungskapazität", "44976", "253084")},
    {"title": "Trägertausch (Bitswap)", "val": [{"us": "an", "ds": "an"}]},
    {"title": ""},
    {"unit": "ms", **_row("Latenz", "<1", "<1")},
    {"title": ""},
    {"unit": "dB", **_row("Störabstandsmarge", "8", "9")},
    {"unit": "dB", **_row("Leitungsdämpfung", "13", "17")},
    {"unit": "m", "title": "ungefähre Leitungslänge", "val": ["260"]},
    {"title": ""},
    {"title": "Profil", "val": ["35b"]},
    {"title": "G.Vector", "val": [{"us": "full", "ds": "full"}]},
    {"title": ""},
    {"title": "Trägersatz", "val": ["B43"]},
]


def page(rows: list | None = None, negotiated: list | None = None) -> dict:
    return {
        "pid": "dslStat",
        "data": {
            "isConnected": True,
            "negotiatedValues": negotiated or NEGOTIATED_VALUES,
            "errorCounters": rows or ERROR_COUNTERS,
        },
    }


DSL_STAT_RAW = page()


class TestParseDslStat:
    def test_reads_both_directions_of_every_counter(self):
        issues = Issues()

        counters = parse_dsl_stat_response(page(), issues)["errors"]

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

    def test_reads_the_line_data(self):
        issues = Issues()

        line = parse_dsl_stat_response(page(), issues)["line"]

        assert line == {
            "length_meters": 260.0,
            "profile": "35b",
            "carrier_set": "B43",
            "vectoring_receive": "full",
            "vectoring_transmit": "full",
        }
        assert issues.messages == []

    def test_unknown_section_layout_gives_only_the_line_length(self):
        # e.g. an ADSL box: profile, carrier set and vectoring are not guessed.
        negotiated = [row for row in NEGOTIATED_VALUES if row.get("title") != "Trägersatz"]
        issues = Issues()

        line = parse_dsl_stat_response(page(negotiated=negotiated), issues)["line"]

        assert line == {
            "length_meters": 260.0,
            "profile": None,
            "carrier_set": None,
            "vectoring_receive": None,
            "vectoring_transmit": None,
        }
        assert issues.messages == []

    def test_defective_line_length_is_recorded(self):
        negotiated = copy.deepcopy(NEGOTIATED_VALUES)
        negotiated[8]["val"] = ["n/a"]
        issues = Issues()

        line = parse_dsl_stat_response(page(negotiated=negotiated), issues)["line"]

        assert line["length_meters"] is None
        assert line["profile"] == "35b"
        assert [m.split(", got")[0] for m in issues.messages] == [
            "dslStat negotiatedValues[8] line length: expected a number"
        ]

    @pytest.mark.parametrize("raw", [{"data": {"isConnected": False}}, {"data": []}])
    def test_line_without_counters_gives_none_and_no_issue(self, raw):
        issues = Issues()

        statistics = parse_dsl_stat_response(raw, issues)

        assert all(v == NONE for v in statistics["errors"].values())
        assert set(statistics["line"].values()) == {None}
        assert issues.messages == []

    def test_missing_section_gives_none_and_is_recorded(self):
        issues = Issues()

        counters = parse_dsl_stat_response(page(ERROR_COUNTERS[:12]), issues)["errors"]

        assert counters["corrected_dtus_15_minutes"] == {"receive": 25.0, "transmit": 9.0}
        assert counters["uncorrected_dtus_minute"] == NONE
        assert issues.messages == ["dslStat errorCounters: expected 4 sections, found 3"]

    def test_defective_value_gives_none_and_is_recorded(self):
        rows = copy.deepcopy(ERROR_COUNTERS)
        rows[1]["val"][0]["ds"] = "n/a"
        issues = Issues()

        counters = parse_dsl_stat_response(page(rows), issues)["errors"]

        assert counters["errored_seconds"] == {"receive": None, "transmit": 0.0}
        assert counters["severely_errored_seconds"] == {"receive": 1.0, "transmit": 0.0}
        assert [m.split(", got")[0] for m in issues.messages] == [
            "dslStat errorCounters section 0 errored_seconds val[0] ds: expected a number"
        ]

    def test_value_of_the_wrong_shape_gives_none_and_is_recorded(self):
        rows = copy.deepcopy(ERROR_COUNTERS)
        rows[1]["val"] = "none"
        issues = Issues()

        counters = parse_dsl_stat_response(page(rows), issues)["errors"]

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
            {"data": {"negotiatedValues": ["not an object"]}},
        ],
    )
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_dsl_stat_response(raw, Issues())


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanDslLineStatistics:
    def _collect(self, mock_fritzconnection: MagicMock, client: MagicMock) -> dict:
        return collect_device(
            mock_fritzconnection,
            services("DeviceInfo", "WanDSLInterfaceConfig"),
            webui_client=client,
            name="FritzDsl",
        )

    @staticmethod
    def _samples(by_name: dict, name: str) -> dict[tuple, float]:
        return {
            tuple(v for k, v in s.labels.items() if k not in ("serial", "friendly_name")): s.value
            for s in by_name[name].samples
        }

    def test_exports_counters_per_direction_and_window(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = DSL_STAT_RAW

        by_name = self._collect(mock_fritzconnection, client)

        client.fetch_page.assert_called_once_with("dslStat")
        assert self._samples(by_name, "fritz_dsl_errored_seconds") == {("rx",): 14, ("tx",): 0}
        assert self._samples(by_name, "fritz_dsl_severely_errored_seconds") == {
            ("rx",): 1,
            ("tx",): 0,
        }
        assert self._samples(by_name, "fritz_dsl_corrected_dtus") == {
            ("rx", "minute"): 1.8,
            ("tx", "minute"): 0.2,
            ("rx", "15_minutes"): 25,
            ("tx", "15_minutes"): 9,
        }
        assert self._samples(by_name, "fritz_dsl_crc_errors")[("rx", "15_minutes")] == 3
        assert self._samples(by_name, "fritz_dsl_uncorrected_dtus")[("rx", "minute")] == 0.5

    def test_exports_line_length_and_info(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = DSL_STAT_RAW

        by_name = self._collect(mock_fritzconnection, client)

        assert self._samples(by_name, "fritz_dsl_line_length_meters") == {(): 260}
        assert self._samples(by_name, "fritz_dsl_line_info") == {("35b", "B43", "full", "full"): 1}

    def test_line_without_profile_exports_no_info(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = page(negotiated=NEGOTIATED_VALUES[:9])

        by_name = self._collect(mock_fritzconnection, client)

        assert by_name["fritz_dsl_line_info"].samples == []
        assert self._samples(by_name, "fritz_dsl_line_length_meters") == {(): 260}

    def test_web_ui_error_exports_nothing_and_is_reported(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")

        with caplog.at_level(logging.WARNING):
            by_name = self._collect(mock_fritzconnection, client)

        assert by_name["fritz_dsl_errored_seconds"].samples == []
        assert "Could not read WanDslLineStatistics data from FritzDsl" in caplog.text

    def test_defective_counter_is_exported_as_missing(self, mock_fritzconnection: MagicMock):
        rows = copy.deepcopy(ERROR_COUNTERS)
        rows[1]["val"][0]["ds"] = "n/a"
        client = MagicMock()
        client.fetch_page.return_value = page(rows=rows)

        by_name = self._collect(mock_fritzconnection, client)

        assert self._samples(by_name, "fritz_dsl_errored_seconds") == {("tx",): 0}
        assert len(by_name["fritz_dsl_severely_errored_seconds"].samples) == 2
