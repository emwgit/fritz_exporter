"""Tests for the parser of the smart home page sh (fritz_smart_home.py)."""

import pytest

from fritzexporter.fritz_smart_home import parse_smart_home_currents
from fritzexporter.reply import DataError, Issues


def _device(ain: object, *skills: dict) -> dict:
    return {"actorIdentificationNumber": ain, "units": [{"skills": list(skills)}]}


def _multimeter(current: object) -> dict:
    return {"type": "SmartHomeMultimeter", "electricCurrentInAmpere": current}


class TestParseSmartHomeCurrents:
    def test_reads_the_current_by_ain_from_the_multimeter_skill_only(self):
        raw = {
            "data": {
                "devices": [
                    _device("08761 0000001", {"type": "SmartHomeSwitch"}, _multimeter(0.6345)),
                    _device("08761 0000002", {"type": "SmartHomeThermostat"}),
                    _device("08761 0000003", _multimeter(0.0012)),
                ]
            }
        }
        issues = Issues()

        currents = parse_smart_home_currents(raw, issues)

        assert currents == {"08761 0000001": 0.6345, "08761 0000003": 0.0012}
        assert issues.messages == []

    def test_a_device_without_ain_or_units_is_left_out(self):
        raw = {"data": {"devices": [{"units": []}, {"actorIdentificationNumber": "1"}]}}

        assert parse_smart_home_currents(raw, Issues()) == {}

    def test_an_empty_data_object_means_no_devices(self):
        assert parse_smart_home_currents({"data": {}}, Issues()) == {}

    def test_a_defective_value_skips_the_device_and_is_recorded(self):
        raw = {
            "data": {
                "devices": [
                    _device("1", _multimeter("a lot")),
                    _device("2", _multimeter(0.5)),
                ]
            }
        }
        issues = Issues()

        currents = parse_smart_home_currents(raw, issues)

        assert currents == {"2": 0.5}
        assert len(issues.messages) == 1

    @pytest.mark.parametrize("raw", [{}, {"other": 1}, "login page", None])
    def test_a_reply_without_data_raises_a_data_error(self, raw: object):
        with pytest.raises(DataError):
            parse_smart_home_currents(raw, Issues())
