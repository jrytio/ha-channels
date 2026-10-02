"""Fixtures for the Home Assistant layer."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.channels.const import CONF_KIND, DOMAIN, KIND_APP, KIND_DVR
from custom_components.channels.lib import AppClient, AppStatus, DvrClient

from .fixtures import DVR_STATUS, FAVORITE_CHANNELS, STATUS_RECORDING

OFFICE = "media_player.office_channels"
LIVING_ROOM = "media_player.living_room_channels"

_COMMANDS = (
    "play_channel",
    "play_recording",
    "pause",
    "resume",
    "stop",
    "seek",
    "seek_forward",
    "seek_backward",
    "skip_forward",
    "skip_backward",
    "toggle_mute",
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture(autouse=True)
def auto_mock_zeroconf(mock_zeroconf, mock_async_zeroconf):
    """The manifest lists zeroconf, so Home Assistant sets it up; fake it."""


def status_of(body: dict, sampled_at: float = 1000.0) -> AppStatus:
    return AppStatus.from_dict(body, sampled_at)


def make_app_client(body: dict = STATUS_RECORDING) -> AsyncMock:
    client = AsyncMock(spec=AppClient)
    client.status.return_value = status_of(body)
    client.favorite_channels.return_value = FAVORITE_CHANNELS
    for command in _COMMANDS:
        getattr(client, command).return_value = status_of(body)
    return client


@pytest.fixture
def app_clients() -> dict[str, AsyncMock]:
    """One fake app client per host; tests reach them by IP address."""
    return {}


@pytest.fixture
def dvr_client() -> AsyncMock:
    client = AsyncMock(spec=DvrClient)
    client.status.return_value = DVR_STATUS
    return client


@pytest.fixture(autouse=True)
def patch_clients(app_clients, dvr_client):
    def app_factory(host, session, port=57000, clock=None):
        return app_clients.setdefault(host, make_app_client())

    with (
        patch("custom_components.channels.AppClient", side_effect=app_factory),
        patch(
            "custom_components.channels.config_flow.AppClient",
            side_effect=app_factory,
        ),
        patch("custom_components.channels.DvrClient", return_value=dvr_client),
        patch(
            "custom_components.channels.config_flow.DvrClient",
            return_value=dvr_client,
        ),
    ):
        yield


def app_entry(hass: HomeAssistant, name: str, host: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"{name} Channels",
        unique_id=f"app_{name.lower().replace(' ', '-')}.local",
        data={CONF_KIND: KIND_APP, CONF_HOST: host, CONF_PORT: 57000},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def office_entry(hass: HomeAssistant) -> MockConfigEntry:
    return app_entry(hass, "Office", "10.0.0.2")


@pytest.fixture
def living_room_entry(hass: HomeAssistant) -> MockConfigEntry:
    return app_entry(hass, "Living Room", "10.0.0.1")


@pytest.fixture
def dvr_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Channels DVR nas6",
        unique_id="dvr_dvr-nas6.local",
        data={CONF_KIND: KIND_DVR, CONF_HOST: "10.0.0.9", CONF_PORT: 8089},
    )
    entry.add_to_hass(hass)
    return entry


async def setup_integration(hass: HomeAssistant) -> None:
    """Set up the integration and every entry added to hass so far."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
