"""Tests for the WiFi link speeds of the network devices (fritz_net_devices.py)."""

import copy
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_net_devices import parse_net_dev_response
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError, Issues

from .collect import collect_device, services


def _device(mac: str, *texts: str, kind: str = "wlan") -> dict[str, Any]:
    properties = [{"onclick": "", "txt": text, "icon": "", "link": ""} for text in texts]
    return {"mac": mac, "type": kind, "UID": "landevice1", "port": "WLAN", "properties": properties}


# Shape of the netDev page of a FRITZ!Box 5690 Pro (Fritz!OS 8.x) with invented devices.
NET_DEV_RAW: dict[str, Any] = {
    "pid": "netDev",
    "data": {
        "active": [
            _device("02:00:00:00:00:01", "5 GHz, 300 / 300 Mbit/s"),
            _device("02:00:00:00:00:02", "2,4 GHz, 144 / 1 Mbit/s"),
            _device(
                "02:00:00:00:00:03",
                "5 GHz, 1441 / 2450 Mbit/s, MLO",
                "6 GHz, 2594 / 3629 Mbit/s, MLO",
            ),
            _device("02:00:00:00:00:04", "LAN 1 mit 1 Gbit/s", kind="ethernet"),
        ],
        "passive": [_device("02:00:00:00:00:05", kind="unknown")],
    },
}


def _links(raw: object, issues: Issues | None = None) -> dict[tuple[str, str], tuple[float, float]]:
    links = parse_net_dev_response(raw, issues or Issues())
    return {(link["mac"], link["band"]): (link["receive_bps"], link["transmit_bps"]) for link in links}


class TestParseNetDev:
    def test_reads_both_speeds_in_bit_per_second(self):
        links = _links(NET_DEV_RAW)
        assert links[("02:00:00:00:00:01", "5GHz")] == (300e6, 300e6)
        assert links[("02:00:00:00:00:03", "6GHz")] == (2594e6, 3629e6)

    def test_decimal_comma_and_other_units(self):
        raw = {"data": {"active": [_device("02:00:00:00:00:06", "2,4 GHz, 1,5 / 800 kbit/s")]}}
        assert _links(raw) == {("02:00:00:00:00:06", "2.4GHz"): (1.5e3, 800e3)}

    def test_client_on_several_bands_has_one_entry_per_band(self):
        links = _links(NET_DEV_RAW)
        assert {band for mac, band in links if mac == "02:00:00:00:00:03"} == {"5GHz", "6GHz"}

    def test_devices_without_a_wifi_link_give_nothing(self):
        macs = {mac for mac, _band in _links(NET_DEV_RAW)}
        assert "02:00:00:00:00:04" not in macs  # Ethernet
        assert "02:00:00:00:00:05" not in macs  # passive

    def test_mac_is_upper_case(self):
        raw = {"data": {"active": [_device("aa:bb:cc:dd:ee:ff", "5 GHz, 6 / 6 Mbit/s")]}}
        assert list(_links(raw)) == [("AA:BB:CC:DD:EE:FF", "5GHz")]

    def test_device_listed_twice_gives_the_first_entry(self):
        raw = copy.deepcopy(NET_DEV_RAW)
        raw["data"]["active"].append(_device("02:00:00:00:00:01", "5 GHz, 1 / 1 Mbit/s"))
        assert _links(raw)[("02:00:00:00:00:01", "5GHz")] == (300e6, 300e6)

    def test_unknown_band_is_left_out(self):
        raw = {"data": {"active": [_device("02:00:00:00:00:06", "60 GHz, 6 / 6 Mbit/s")]}}
        assert _links(raw) == {}

    def test_defective_device_is_skipped_and_recorded(self):
        raw = copy.deepcopy(NET_DEV_RAW)
        raw["data"]["active"].insert(1, {"type": "wlan", "properties": []})
        issues = Issues()

        links = _links(raw, issues)

        assert ("02:00:00:00:00:02", "2.4GHz") in links
        assert len(issues.messages) == 1
        assert issues.messages[0].startswith("netDev active[1] skipped")

    def test_element_that_is_no_object_is_skipped_and_recorded(self):
        raw = copy.deepcopy(NET_DEV_RAW)
        raw["data"]["active"].append("not an object")
        issues = Issues()

        assert len(_links(raw, issues)) == 4
        assert len(issues.messages) == 1

    @pytest.mark.parametrize("raw", [{"pid": "netDev"}, [], {"data": "not an object"}])
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_net_dev_response(raw, Issues())

    def test_empty_data_gives_no_links(self):
        assert parse_net_dev_response({"data": {}}, Issues()) == []


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWlanClientLinkSpeeds:
    def _collect(self, mock_fritzconnection: MagicMock, client: MagicMock) -> dict:
        return collect_device(
            mock_fritzconnection,
            services("DeviceInfo", "WlanConfigurationInfo"),
            webui_client=client,
            name="FritzWifi",
        )

    def test_exports_both_directions_per_client_and_band(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = NET_DEV_RAW

        by_name = self._collect(mock_fritzconnection, client)

        client.fetch_page.assert_called_once_with("netDev")
        speeds = {
            (s.labels["client_mac"], s.labels["band"], s.labels["direction"]): s.value
            for s in by_name["fritz_wifi_client_link_speed_bps"].samples
        }
        assert speeds[("02:00:00:00:00:02", "2.4GHz", "rx")] == 144e6
        assert speeds[("02:00:00:00:00:02", "2.4GHz", "tx")] == 1e6
        assert speeds[("02:00:00:00:00:03", "5GHz", "tx")] == 2450e6
        assert len(speeds) == 8

    def test_web_ui_error_exports_nothing_and_is_reported(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")

        with caplog.at_level(logging.WARNING):
            by_name = self._collect(mock_fritzconnection, client)

        assert by_name["fritz_wifi_client_link_speed_bps"].samples == []
        assert "Could not read WlanClientLinkSpeeds data from FritzWifi" in caplog.text
