"""Tests for the WAN throughput per QoS class (fritz_wan_qos.py)."""

import copy
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_wan_qos import parse_wan_qos_response
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError, Issues

from .collect import collect_device, services

# Shape of the netMoni page of a FRITZ!Box 5690 Pro (Fritz!OS 8.x), values in bytes
# per second as the box sends them, invented.
WAN_QOS_RAW: dict[str, Any] = {
    "pid": "netMoni",
    "data": {
        "show_guest": True,
        "sync_groups": [
            {
                "name": "sync_dsl",
                "_node": "sg0",
                "mode": "VDSL",
                "downstream": 219741000.0,
                "upstream": 41349000.0,
                "us_realtime_bps_curr": [100, 300],
                "us_important_bps_curr": [0, 0],
                "us_default_bps_curr": [1000, 3000],
                "us_background_bps_curr": [0] * 20,
                "ds_bps_curr": [8000, 2000, 5000, 5000],
                "ds_mc_bps_curr": [0, 4000],
                "ds_guest_bps_curr": [10, 30],
                "guest_us_bps": [50],
            }
        ],
    },
}


def _throughput(raw: object, issues: Issues | None = None) -> dict[tuple[str, str], Any]:
    return {
        (entry["direction"], entry["qos_class"]): entry["bits_per_second"]
        for entry in parse_wan_qos_response(raw, issues or Issues())["classes"]
    }


class TestParseWanQos:
    def test_mean_of_the_samples_is_converted_from_bytes_to_bits(self):
        throughput = _throughput(WAN_QOS_RAW)
        assert throughput[("tx", "realtime")] == pytest.approx(200 * 8)
        assert throughput[("tx", "default")] == pytest.approx(2000 * 8)
        assert throughput[("rx", "internet")] == pytest.approx(5000 * 8)
        assert throughput[("rx", "multicast")] == pytest.approx(2000 * 8)

    def test_every_class_of_the_page_is_exported(self):
        assert sorted(_throughput(WAN_QOS_RAW)) == [
            ("rx", "internet"),
            ("rx", "multicast"),
            ("tx", "background"),
            ("tx", "default"),
            ("tx", "important"),
            ("tx", "realtime"),
        ]

    def test_link_is_the_name_of_the_sync_group_without_its_prefix(self):
        links = {entry["link"] for entry in parse_wan_qos_response(WAN_QOS_RAW, Issues())["classes"]}
        assert links == {"dsl"}

    def test_guest_network_is_apart_from_the_classes(self):
        guests = parse_wan_qos_response(WAN_QOS_RAW, Issues())["guests"]
        assert {(g["direction"], g["bits_per_second"]) for g in guests} == {
            ("tx", 50 * 8),
            ("rx", 20 * 8),
        }

    def test_every_sync_group_is_exported(self):
        raw = copy.deepcopy(WAN_QOS_RAW)
        raw["data"]["sync_groups"].append(
            copy.deepcopy(raw["data"]["sync_groups"][0]) | {"name": "sync_lte"}
        )
        links = {entry["link"] for entry in parse_wan_qos_response(raw, Issues())["classes"]}
        assert links == {"dsl", "lte"}

    @pytest.mark.parametrize("change", ["absent", "empty"])
    def test_class_without_samples_is_left_out(self, change):
        raw = copy.deepcopy(WAN_QOS_RAW)
        group = raw["data"]["sync_groups"][0]
        if change == "absent":
            del group["ds_mc_bps_curr"]
        else:
            group["ds_mc_bps_curr"] = []
        issues = Issues()

        throughput = _throughput(raw, issues)

        assert ("rx", "multicast") not in throughput
        assert ("rx", "internet") in throughput
        assert issues.messages == []

    def test_defective_sample_skips_the_class_and_is_recorded(self):
        raw = copy.deepcopy(WAN_QOS_RAW)
        raw["data"]["sync_groups"][0]["us_default_bps_curr"] = [1000, "x"]
        issues = Issues()

        throughput = _throughput(raw, issues)

        assert ("tx", "default") not in throughput
        assert ("tx", "realtime") in throughput
        assert len(issues.messages) == 1
        assert "netMoni sync_groups[0] us_default_bps_curr skipped" in issues.messages[0]

    def test_group_without_a_name_is_skipped_and_recorded(self):
        raw = copy.deepcopy(WAN_QOS_RAW)
        del raw["data"]["sync_groups"][0]["name"]
        issues = Issues()

        assert _throughput(raw, issues) == {}
        assert len(issues.messages) == 1
        assert issues.messages[0].startswith("netMoni sync_groups[0] skipped")

    @pytest.mark.parametrize("raw", [{"pid": "netMoni"}, [], {"data": "not an object"}])
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_wan_qos_response(raw, Issues())

    def test_empty_data_gives_nothing(self):
        assert parse_wan_qos_response({"data": {}}, Issues()) == {"classes": [], "guests": []}


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanQosThroughput:
    def _collect(self, mock_fritzconnection: MagicMock, client: MagicMock) -> dict:
        return collect_device(
            mock_fritzconnection,
            services("DeviceInfo", "WanCommonInterfaceConfig"),
            webui_client=client,
            name="FritzWan",
        )

    def test_exports_the_throughput_per_class(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = WAN_QOS_RAW

        by_name = self._collect(mock_fritzconnection, client)

        client.fetch_page.assert_called_once_with("netMoni", xhrId="updateGraphs")
        samples = {
            (s.labels["link"], s.labels["direction"], s.labels["qos_class"]): s.value
            for s in by_name["fritz_wan_qos_throughput_bps"].samples
        }
        assert samples[("dsl", "rx", "internet")] == pytest.approx(40000)
        assert samples[("dsl", "tx", "default")] == pytest.approx(16000)
        assert len(samples) == 6
        guest = {
            (s.labels["link"], s.labels["direction"]): s.value
            for s in by_name["fritz_wan_guest_throughput_bps"].samples
        }
        assert guest == {("dsl", "tx"): pytest.approx(400), ("dsl", "rx"): pytest.approx(160)}

    def test_web_ui_error_exports_nothing_and_is_reported(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")

        with caplog.at_level(logging.WARNING):
            by_name = self._collect(mock_fritzconnection, client)

        assert by_name["fritz_wan_qos_throughput_bps"].samples == []
        assert "Could not read WanQosThroughput data from FritzWan" in caplog.text
