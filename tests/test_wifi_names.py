"""The names of the WLANConfiguration services depend on the bands of the box."""

from functools import partial
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from .collect import collect_device
from .fc_services_mock import call_action_mock, fc_services_capabilities


def _band_names(mock_fritzconnection: MagicMock, wlans: int, third_band: str | None) -> dict:
    actions = fc_services_capabilities["WlanConfigurationInfo"]["WLANConfiguration1"]
    overrides: dict[tuple[str, str], Any] = {}
    if third_band is not None:
        overrides[("WLANConfiguration3", "GetInfo")] = {"NewX_AVM-DE_FrequencyBand": third_band}
    if wlans == 4:
        # The mock knows three WLANs; the fourth answers like the third.
        for action in ("GetInfo", "GetTotalAssociations", "GetPacketStatistics"):
            overrides[("WLANConfiguration4", action)] = partial(
                lambda action, **_: call_action_mock("WLANConfiguration3", action), action
            )
    by_name = collect_device(
        mock_fritzconnection,
        {f"WLANConfiguration{i}": actions for i in range(1, wlans + 1)},
        overrides=overrides,
    )
    return {
        s.labels["wifi_index"]: s.labels["wifi_name"] for s in by_name["fritz_wifi_status"].samples
    }


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWifiNames:
    @pytest.mark.parametrize("third_band", [None, "2400"])
    def test_third_wlan_is_the_guest_network_without_a_6_ghz_radio(
        self, mock_fritzconnection: MagicMock, third_band: str | None
    ):
        assert _band_names(mock_fritzconnection, 3, third_band) == {
            "1": "2.4GHz",
            "2": "5GHz",
            "3": "Guest",
        }

    def test_third_wlan_is_6_ghz_and_the_fourth_the_guest_network_on_a_tri_band_box(
        self, mock_fritzconnection: MagicMock
    ):
        assert _band_names(mock_fritzconnection, 4, "6000") == {
            "1": "2.4GHz",
            "2": "5GHz",
            "3": "6GHz",
            "4": "Guest",
        }

    def test_fourth_wlan_keeps_its_name_without_a_6_ghz_radio(self, mock_fritzconnection: MagicMock):
        assert _band_names(mock_fritzconnection, 4, None)["4"] == "WLAN4"
