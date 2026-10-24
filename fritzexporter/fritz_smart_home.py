"""The electric current of smart home sockets from the Fritz!Box web interface.

The AHA device list (:mod:`fritzexporter.fritz_aha`) has the power, the energy and the
voltage of a socket, but no current. The web UI shows it, read from the page
``data.lua?page=sh``: a list of devices, each with its AIN
(``actorIdentificationNumber``, spelled as in the AHA list) and units; a unit has
skills, and the skill ``SmartHomeMultimeter`` holds ``electricCurrentInAmpere`` next to
the voltage, power and energy. The generic web login and the ``data.lua`` fetching are
in :mod:`fritzexporter.fritz_webui`; this module holds the page name and the parser.
"""

from __future__ import annotations

from typing import Final

from fritzexporter.reply import Issues, Reply

__all__ = [
    "SMART_HOME_PAGE",
    "parse_smart_home_currents",
]

#: The ``data.lua`` page that lists the smart home devices with their skills.
SMART_HOME_PAGE: Final[str] = "sh"

_MULTIMETER_SKILL: Final[str] = "SmartHomeMultimeter"


def parse_smart_home_currents(raw: object, issues: Issues) -> dict[str, float]:
    """Return the electric current in A by AIN, for the devices that report one.

    A device or a value that is defective is skipped and recorded in ``issues``; a
    reply of the wrong shape, or without ``data``, raises
    :class:`~fritzexporter.reply.DataError`.
    """
    data = Reply(raw, "sh").require_key("data").opt_obj("data")
    currents: dict[str, float] = {}
    if data is None:
        return currents
    for device_index, device in issues.objects(data, "devices"):
        what = f"sh devices[{device_index}]"
        with issues.item(what):
            ain = device.opt_text("actorIdentificationNumber")
            if ain is None:
                continue
            for _, unit in issues.objects(device, "units"):
                for _, skill in issues.objects(unit, "skills"):
                    if skill.opt_text("type") != _MULTIMETER_SKILL:
                        continue
                    current = issues.opt_num(skill, "electricCurrentInAmpere")
                    if current is not None:
                        currents[ain] = current
    return currents
