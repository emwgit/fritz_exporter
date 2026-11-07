from typing import Any
from unittest.mock import MagicMock, patch
from xml.etree.ElementTree import ParseError

import pytest
import requests
from fritzconnection.core.exceptions import FritzConnectionException
from requests.adapters import HTTPAdapter
from requests.auth import HTTPDigestAuth

from fritzexporter.tr064_remote import (
    ConnectionOptions,
    FritzUnreachableError,
    Tr064RemoteAccessSession,
    create_fritz_connection,
    create_web_session,
    rewrite_tr064_remote_url,
)


class TestRewriteTr064RemoteUrl:
    def test_prepends_prefix_to_description(self) -> None:
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/tr64desc.xml")
            == "https://box.example:11243/tr064/tr64desc.xml"
        )

    def test_prepends_prefix_to_soap_control_url(self) -> None:
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/upnp/control/deviceinfo")
            == "https://box.example:11243/tr064/upnp/control/deviceinfo"
        )

    def test_prepends_prefix_to_scpd_path(self) -> None:
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/deviceinfoSCPD.xml")
            == "https://box.example:11243/tr064/deviceinfoSCPD.xml"
        )

    def test_idempotent_when_prefix_already_present(self) -> None:
        url = "https://box.example:11243/tr064/tr64desc.xml"
        assert rewrite_tr064_remote_url(url) == url

    def test_preserves_query_string(self) -> None:
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/foo.xml?x=1")
            == "https://box.example:11243/tr064/foo.xml?x=1"
        )


class TestTr064RemoteAccessSession:
    def test_request_rewrites_url(self) -> None:
        session = Tr064RemoteAccessSession()
        seen: list[str] = []

        def fake_request(self, method, url, *args: object, **kwargs: Any) -> MagicMock:  # noqa: ANN001
            seen.append(url)
            response = MagicMock()
            response.status_code = 200
            return response

        with patch.object(requests.Session, "request", fake_request):
            session.request("GET", "https://box.example:11243/tr64desc.xml")

        assert seen == ["https://box.example:11243/tr064/tr64desc.xml"]

    def test_rewrite_survives_fritzconnection_adapter_remount(self) -> None:
        """FritzConnection replaces mounted adapters after Session().

        A Session.request override must still rewrite URLs after that remount.
        """
        session = Tr064RemoteAccessSession()
        session.mount("https://", HTTPAdapter())  # simulates FritzConnection
        seen: list[str] = []

        def fake_request(self, method, url, *args: object, **kwargs: Any) -> MagicMock:  # noqa: ANN001
            seen.append(url)
            response = MagicMock()
            response.status_code = 200
            return response

        with patch.object(requests.Session, "request", fake_request):
            session.request("GET", "https://box.example:11243/igddesc.xml")

        assert seen == ["https://box.example:11243/tr064/igddesc.xml"]
        assert type(session.adapters["https://"]) is HTTPAdapter


class TestCreateFritzConnection:
    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_connection_timeout_applies_to_aha_http_requests(self, mock_fc: MagicMock) -> None:
        captured_sessions: list[requests.Session] = []

        def fake_fc(**_kwargs: object) -> MagicMock:
            session = requests.Session()
            captured_sessions.append(session)
            return MagicMock()

        mock_fc.side_effect = fake_fc

        create_fritz_connection(
            address="box.example",
            user="user",
            password="password",
            connection=ConnectionOptions(connection_timeout=10),
        )

        with patch.object(requests.Session, "request", return_value=MagicMock()) as request:
            captured_sessions[0].get("https://box.example/webservices/homeautoswitch.lua")

        assert request.call_args.kwargs["timeout"] == 10

    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_request_timeout_becomes_fritz_connection_exception(self, mock_fc: MagicMock) -> None:

        captured_sessions: list[requests.Session] = []

        def fake_fc(**_kwargs: object) -> MagicMock:
            session = requests.Session()
            captured_sessions.append(session)
            return MagicMock()

        mock_fc.side_effect = fake_fc

        create_fritz_connection(
            address="box.example",
            user="user",
            password="password",
            connection=ConnectionOptions(connection_timeout=10),
        )

        with (
            patch.object(requests.Session, "request", side_effect=requests.ReadTimeout),
            pytest.raises(FritzConnectionException, match=r"did not answer \(ReadTimeout\)"),
        ):
            captured_sessions[0].get("https://box.example/webservices/homeautoswitch.lua")

    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_remote_access_uses_rewriting_session(self, mock_fc: MagicMock) -> None:
        captured_sessions: list[requests.Session] = []

        def fake_fc(**_kwargs: object) -> MagicMock:
            session = requests.Session()
            # Simulate FritzConnection remounting a plain adapter
            session.mount("https://", HTTPAdapter())
            captured_sessions.append(session)
            return MagicMock()

        mock_fc.side_effect = fake_fc

        create_fritz_connection(
            address="box.example",
            user="user",
            password="pass",
            connection=ConnectionOptions(use_tls=True, port=11243, remote_access=True),
        )

        assert len(captured_sessions) == 1
        assert isinstance(captured_sessions[0], Tr064RemoteAccessSession)
        mock_fc.assert_called_once_with(
            address="box.example",
            user="user",
            password="pass",
            timeout=10,
            use_tls=True,
            port=11243,
        )

    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_parse_error_becomes_fritz_connection_exception(self, mock_fc: MagicMock) -> None:

        mock_fc.side_effect = ParseError("not well-formed")

        options = ConnectionOptions(use_tls=True, port=11243, remote_access=True)
        with pytest.raises(FritzConnectionException, match="not XML"):
            create_fritz_connection(
                address="box.example",
                user="user",
                password="pass",
                connection=options,
            )


class TestCreateWebSession:
    @staticmethod
    def _fc() -> MagicMock:
        fc = MagicMock()
        fc.session = requests.Session()
        fc.session.verify = False
        fc.session.auth = HTTPDigestAuth("munin", "secret")
        return fc

    def test_is_a_session_of_its_own(self) -> None:
        fc = self._fc()
        web = create_web_session(fc, ConnectionOptions(connection_timeout=10))
        assert web is not fc.session
        assert web.auth is not fc.session.auth

    def test_keeps_the_login_and_tls_settings(self) -> None:
        web = create_web_session(self._fc(), ConnectionOptions(connection_timeout=10))
        assert web.verify is False
        assert isinstance(web.auth, HTTPDigestAuth)
        assert (web.auth.username, web.auth.password) == ("munin", "secret")

    def test_connection_timeout_applies(self) -> None:
        web = create_web_session(self._fc(), ConnectionOptions(connection_timeout=10))
        with patch.object(requests.Session, "request", return_value=MagicMock()) as request:
            web.get("https://box.example/meshlist.lua")
        assert request.call_args.kwargs["timeout"] == 10

    def test_unanswered_request_is_unreachable(self) -> None:
        web = create_web_session(self._fc(), ConnectionOptions(connection_timeout=10))
        with (
            patch.object(requests.Session, "request", side_effect=requests.ReadTimeout),
            pytest.raises(FritzUnreachableError),
        ):
            web.get("https://box.example/meshlist.lua")

    def test_remote_access_rewrites_the_path_like_the_connection(self) -> None:
        options = ConnectionOptions(use_tls=True, port=11243, remote_access=True)
        web = create_web_session(self._fc(), options)
        with patch.object(requests.Session, "request", return_value=MagicMock()) as request:
            web.get("https://box.example:11243/meshlist.lua")
        assert request.call_args.args[1] == "https://box.example:11243/tr064/meshlist.lua"
