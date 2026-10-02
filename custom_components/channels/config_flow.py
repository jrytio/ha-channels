"""Config flow for the Channels integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import probatio as vol

from .const import (
    CONF_KIND,
    CONF_SYNC_OFFSET_MS,
    DOMAIN,
    KIND_APP,
    KIND_DVR,
    MAX_SYNC_OFFSET_MS,
    ZEROCONF_APP,
)
from .lib import (
    DEFAULT_APP_PORT,
    DEFAULT_DVR_PORT,
    AppClient,
    ChannelsError,
    DvrClient,
)

DEFAULT_PORTS = {KIND_APP: DEFAULT_APP_PORT, KIND_DVR: DEFAULT_DVR_PORT}


def unique_id_for(kind: str, hostname: str) -> str:
    """Return the unique ID for a device: its kind and its host name."""
    return f"{kind}_{hostname.rstrip('.').lower()}"


def default_name(discovery_info: ZeroconfServiceInfo) -> str:
    """Suggest a device name from what Bonjour reports.

    Every Apple TV advertises its app as "Apple TV", so an app is named after
    its host ("Living-Area.local." gives "Living Area"). A server's instance
    name is already meaningful ("nas6").
    """
    if discovery_info.type == ZEROCONF_APP:
        host = discovery_info.hostname.rstrip(".").removesuffix(".local")
        return host.replace("-", " ")
    instance = discovery_info.name.removesuffix(f".{discovery_info.type}")
    return f"Channels DVR {instance}"


class ChannelsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Channels app or a Channels DVR server."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._found: dict[str, Any] = {}

    async def _async_can_connect(self, kind: str, host: str, port: int) -> bool:
        session = async_get_clientsession(self.hass)
        client = (
            AppClient(host, session, port)
            if kind == KIND_APP
            else DvrClient(host, session, port)
        )
        try:
            await client.status()
        except ChannelsError:
            return False
        return True

    def _abort_if_another_dvr(self, kind: str) -> None:
        """Abort when a DVR server is wanted and a different one exists.

        Only one server is supported: the sync actions use a single server's
        recordings and jobs.
        """
        if kind == KIND_DVR and any(
            entry.data.get(CONF_KIND) == KIND_DVR
            for entry in self._async_current_entries()
        ):
            raise AbortFlow("single_dvr_only")

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle an app or server found on the network."""
        kind = KIND_APP if discovery_info.type == ZEROCONF_APP else KIND_DVR
        host = str(discovery_info.ip_address)
        port = discovery_info.port or DEFAULT_PORTS[kind]

        await self.async_set_unique_id(unique_id_for(kind, discovery_info.hostname))
        # A known device that moved to a new address just gets its host updated.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
        # A device added by hand is keyed by the address that was typed in.
        self._async_abort_entries_match({CONF_KIND: kind, CONF_HOST: host})
        self._abort_if_another_dvr(kind)

        if not await self._async_can_connect(kind, host, port):
            return self.async_abort(reason="cannot_connect")

        name = default_name(discovery_info)
        self._found = {
            CONF_KIND: kind,
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_NAME: name,
        }
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user name a discovered device."""
        if user_input is not None:
            # The device may have been added by hand while this discovery waited.
            self._async_abort_entries_match(
                {CONF_KIND: self._found[CONF_KIND], CONF_HOST: self._found[CONF_HOST]}
            )
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    key: self._found[key] for key in (CONF_KIND, CONF_HOST, CONF_PORT)
                },
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=self._found[CONF_NAME]): str}
            ),
            description_placeholders={"host": self._found[CONF_HOST]},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a device entered by hand."""
        errors: dict[str, str] = {}
        if user_input is not None:
            kind = user_input[CONF_KIND]
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT) or DEFAULT_PORTS[kind])
            await self.async_set_unique_id(unique_id_for(kind, host))
            self._abort_if_unique_id_configured()
            self._async_abort_entries_match({CONF_KIND: kind, CONF_HOST: host})
            self._abort_if_another_dvr(kind)
            if await self._async_can_connect(kind, host, port):
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_KIND: kind, CONF_HOST: host, CONF_PORT: port},
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_KIND, default=KIND_APP): SelectSelector(
                            SelectSelectorConfig(
                                options=[KIND_APP, KIND_DVR], translation_key=CONF_KIND
                            )
                        ),
                        vol.Required(CONF_NAME): str,
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_PORT): NumberSelector(
                            NumberSelectorConfig(
                                min=1, max=65535, mode=NumberSelectorMode.BOX
                            )
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ChannelsOptionsFlow:
        """Return the options flow."""
        return ChannelsOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Only apps have options; a server has nothing to tune."""
        # An ignored entry has no data, and no options.
        return config_entry.data.get(CONF_KIND) == KIND_APP


class ChannelsOptionsFlow(OptionsFlow):
    """Per-app options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the sync offset."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SYNC_OFFSET_MS: int(user_input[CONF_SYNC_OFFSET_MS])}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SYNC_OFFSET_MS,
                        default=self.config_entry.options.get(CONF_SYNC_OFFSET_MS, 0),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=-MAX_SYNC_OFFSET_MS,
                            max=MAX_SYNC_OFFSET_MS,
                            step=10,
                            unit_of_measurement="ms",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
