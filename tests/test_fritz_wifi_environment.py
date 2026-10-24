"""Tests for the WiFi airtime and neighbouring networks (fritz_wifi_environment.py)."""

import copy
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.fritz_wifi_environment import parse_wifi_environment_response
from fritzexporter.reply import DataError, Issues

from .collect import collect_device, services


def _airtime(newest: str, older: str, count: int = 120) -> str:
    """Header and samples, newest first: the first 60 are ``newest``, the rest ``older``."""
    return ",".join(["1792869416", "1", str(count), *[newest] * 60, *[older] * (count - 60)])


def _network(band: str, channel: int | str, *, other: bool = True) -> dict[str, Any]:
    entry: dict[str, Any] = {"mac": "00:00:00:00:00:00", "nrssi": -70, "channel": channel}
    entry |= {"ssid": "network", "bandId": band}
    return entry | ({"isEnvNet": True} if other else {})


# Shape of the chan page of a FRITZ!Box 5690 Pro (Fritz!OS 8.x) with invented networks.
WIFI_ENVIRONMENT_RAW: dict[str, Any] = {
    "pid": "chan",
    "data": {
        "hasAirtime": True,
        "bands": ["24ghz", "5ghz", "6ghz"],
        "24ghz": {"active": True, "usedChannels": [6], "airtimedata": _airtime("20:10", "90:50")},
        "5ghz": {"active": True, "usedChannels": [104], "airtimedata": _airtime("8:2", "9:3")},
        "6ghz": {"active": True, "usedChannels": [5], "airtimedata": _airtime("1:0", "1:1")},
        "scanlist": [
            _network("24ghz", 6, other=False),
            _network("24ghz", 6),
            _network("24ghz", "6"),
            _network("24ghz", 1),
            _network("24ghz", 11),
            _network("5ghz", 104),
            _network("5ghz", 36),
            _network("6ghz", 5, other=False),
        ],
    },
}


def _bands(raw: object, issues: Issues | None = None) -> dict[str, Any]:
    return {band["name"]: band for band in parse_wifi_environment_response(raw, issues or Issues())}


class TestParseWifiEnvironment:
    def test_airtime_is_the_mean_of_the_newest_samples_as_a_ratio(self):
        band = _bands(WIFI_ENVIRONMENT_RAW)["2.4GHz"]
        assert band["airtime_receive"] == pytest.approx(0.20)
        assert band["airtime_transmit"] == pytest.approx(0.10)

    def test_counts_networks_of_others_on_the_channel_of_the_band_and_elsewhere(self):
        bands = _bands(WIFI_ENVIRONMENT_RAW)
        assert [
            (b["neighbours_same_channel"], b["neighbours_other_channels"]) for b in bands.values()
        ] == [(2, 2), (1, 1), (0, 0)]

    def test_inactive_band_is_left_out(self):
        raw = copy.deepcopy(WIFI_ENVIRONMENT_RAW)
        raw["data"]["6ghz"]["active"] = False
        assert list(_bands(raw)) == ["2.4GHz", "5GHz"]

    @pytest.mark.parametrize("change", ["no airtime", "hasAirtime false", "empty airtime"])
    def test_band_without_airtime_has_neighbours_only(self, change):
        raw = copy.deepcopy(WIFI_ENVIRONMENT_RAW)
        if change == "no airtime":
            del raw["data"]["5ghz"]["airtimedata"]
        elif change == "hasAirtime false":
            raw["data"]["hasAirtime"] = False
        else:
            raw["data"]["5ghz"]["airtimedata"] = "1792869416,1,3600"
        issues = Issues()

        band = _bands(raw, issues)["5GHz"]

        assert (band["airtime_receive"], band["airtime_transmit"]) == (None, None)
        assert band["neighbours_same_channel"] == 1
        assert issues.messages == []

    def test_defective_sample_gives_no_airtime_and_is_recorded(self):
        raw = copy.deepcopy(WIFI_ENVIRONMENT_RAW)
        raw["data"]["5ghz"]["airtimedata"] = "1792869416,1,3600,8:2,x:3"
        issues = Issues()

        band = _bands(raw, issues)["5GHz"]

        assert band["airtime_receive"] is None
        assert band["neighbours_other_channels"] == 1
        assert len(issues.messages) == 1
        assert "chan 5ghz airtimedata sample 1" in issues.messages[0]

    def test_defective_scan_entry_is_skipped_and_recorded(self):
        raw = copy.deepcopy(WIFI_ENVIRONMENT_RAW)
        raw["data"]["scanlist"].append({"isEnvNet": True, "channel": 6})
        issues = Issues()

        bands = _bands(raw, issues)

        assert bands["2.4GHz"]["neighbours_same_channel"] == 2
        assert len(issues.messages) == 1
        assert issues.messages[0].startswith("scanlist[8] skipped")

    @pytest.mark.parametrize("raw", [{"pid": "chan"}, [], {"data": "not an object"}])
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_wifi_environment_response(raw, Issues())

    def test_empty_data_gives_no_bands(self):
        assert parse_wifi_environment_response({"data": {}}, Issues()) == []


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWlanEnvironment:
    def _collect(self, mock_fritzconnection: MagicMock, client: MagicMock) -> dict:
        return collect_device(
            mock_fritzconnection,
            services("DeviceInfo", "WlanConfigurationInfo"),
            webui_client=client,
            name="FritzWifi",
        )

    def test_exports_airtime_and_neighbours_per_band(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = WIFI_ENVIRONMENT_RAW

        by_name = self._collect(mock_fritzconnection, client)

        client.fetch_page.assert_any_call("chan", xhrId="environment")
        airtime = {
            (s.labels["wifi_name"], s.labels["direction"]): s.value
            for s in by_name["fritz_wifi_airtime_ratio"].samples
        }
        assert airtime[("2.4GHz", "rx")] == pytest.approx(0.20)
        assert airtime[("6GHz", "tx")] == pytest.approx(0)
        neighbours = {
            (s.labels["wifi_name"], s.labels["relation"]): s.value
            for s in by_name["fritz_wifi_neighbor_networks"].samples
        }
        assert neighbours[("2.4GHz", "same_channel")] == 2
        assert neighbours[("5GHz", "other_channels")] == 1
        assert len(neighbours) == 6

    def test_web_ui_error_exports_nothing_and_is_reported(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")

        with caplog.at_level(logging.WARNING):
            by_name = self._collect(mock_fritzconnection, client)

        assert by_name["fritz_wifi_airtime_ratio"].samples == []
        assert "Could not read WlanEnvironment data from FritzWifi" in caplog.text
