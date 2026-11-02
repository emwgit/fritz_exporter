"""Tests for collecting the capabilities of a device in two lanes (fritzdevice.py)."""

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client.core import Metric

from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.fritzdevice import FritzCollector
from fritzexporter.reply import read_action

from .collect import fake_device
from .fc_services_mock import fc_services_devices

# Seconds a lane waits for the other lane; far longer than any real lane takes here.
LANE_WAIT: float = 5.0


def _samples(metrics: Sequence[Metric]) -> list[Any]:
    """The metrics as plain data, in order, so they can be compared."""
    return [
        (metric.name, [(s.name, tuple(sorted(s.labels.items())), s.value) for s in metric.samples])
        for metric in metrics
    ]


def _sequential(collector: FritzCollector) -> list[Metric]:
    """The metrics of the capabilities run one after the other, as before the lanes."""
    return [
        metric
        for name, capa in collector._capability_instances.items()
        for metric in capa.get_metrics(collector.devices, name)
    ]


def _failing_webui() -> MagicMock:
    """A web client whose every read fails; the web UI capabilities skip their data."""
    client = MagicMock()
    for method in ("fetch_page", "fetch_api", "fetch_aha"):
        getattr(client, method).side_effect = FritzWebUiError("login failed")
    return client


def _meet(
    original: Callable[..., Any], mine: threading.Event, other: threading.Event
) -> Callable[..., Any]:
    """Wrap a capability so it sets its own event, then waits for the other lane's event."""

    def get_metrics(devices: list[Any], name: str) -> Any:
        mine.set()
        # Lanes run one after the other would make this wait time out.
        assert other.wait(LANE_WAIT), "the other lane did not start while this one ran"
        return original(devices, name)

    return get_metrics


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestCapabilityLanes:
    def _collector(self, mock_fritzconnection: MagicMock) -> tuple[FritzCollector, MagicMock]:
        device = fake_device(mock_fritzconnection, fc_services_devices["FritzBox 7590"], host_info=True)
        webui = _failing_webui()
        device.webui_client = webui
        collector = FritzCollector()
        collector.register(device)
        return collector, webui

    def test_metrics_come_in_the_order_of_the_sequential_run(self, mock_fritzconnection: MagicMock):
        collector, _ = self._collector(mock_fritzconnection)

        lanes = _samples(collector._collect_capabilities())
        sequential = _samples(_sequential(collector))

        assert lanes
        assert lanes == sequential

    def test_exception_in_one_lane_does_not_stop_the_other(
        self, mock_fritzconnection: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        collector, webui = self._collector(mock_fritzconnection)

        def bug(devices: list[Any], name: str) -> None:
            msg = "bug in a TR-064 capability"
            raise RuntimeError(msg)

        monkeypatch.setattr(collector._capability_instances["DeviceInfo"], "get_metrics", bug)

        with pytest.raises(RuntimeError, match="bug in a TR-064 capability"):
            collector._collect_capabilities()
        # The web UI lane ran on to its reads after the TR-064 lane had failed.
        assert webui.fetch_page.called

    def test_the_lanes_run_at_the_same_time(
        self, mock_fritzconnection: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        collector, _ = self._collector(mock_fritzconnection)
        instances = collector._capability_instances
        web_started = threading.Event()
        tr064_started = threading.Event()

        monkeypatch.setattr(
            instances["WlanEnvironment"],
            "get_metrics",
            _meet(instances["WlanEnvironment"].get_metrics, web_started, tr064_started),
        )
        monkeypatch.setattr(
            instances["DeviceInfo"],
            "get_metrics",
            _meet(instances["DeviceInfo"].get_metrics, tr064_started, web_started),
        )

        assert instances["WlanEnvironment"].uses_webui
        assert not instances["DeviceInfo"].uses_webui
        assert collector._collect_capabilities()

    def test_tr064_calls_of_the_two_lanes_do_not_overlap(self, mock_fritzconnection: MagicMock):
        device = fake_device(mock_fritzconnection, fc_services_devices["FritzBox 7590"])
        fc = mock_fritzconnection.return_value
        answer = fc.call_action.side_effect
        guard = threading.Lock()
        active = 0
        most_at_once = 0

        def call_action(service: str, action: str, **kwargs: Any) -> Any:
            nonlocal active, most_at_once
            with guard:
                active += 1
                most_at_once = max(most_at_once, active)
            time.sleep(0.001)
            with guard:
                active -= 1
            return answer(service, action, **kwargs)

        fc.call_action.side_effect = call_action

        def read_many(_worker: int) -> None:
            for _call in range(20):
                read_action(device, "DeviceInfo1", "GetInfo")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(read_many, range(2)))

        assert most_at_once == 1
