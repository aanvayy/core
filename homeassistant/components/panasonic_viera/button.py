"""Momentary navigation controls for Panasonic Viera TVs."""

from dataclasses import dataclass
from typing import override

from panasonic_viera import Keys

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PanasonicVieraConfigEntry
from .const import ATTR_DEVICE_INFO, ATTR_UDN, DOMAIN

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PanasonicButtonDescription(ButtonEntityDescription):
    """Describe a key already supported by the Panasonic command library."""

    command: Keys


BUTTONS = tuple(
    PanasonicButtonDescription(key=key, translation_key=key, command=command)
    for key, command in (
        ("up", Keys.UP),
        ("down", Keys.DOWN),
        ("left", Keys.LEFT),
        ("right", Keys.RIGHT),
        ("ok", Keys.ENTER),
        ("back", Keys.BACK),
        ("menu", Keys.MENU),
        ("input", Keys.INPUT_KEY),
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicVieraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Attach navigation controls to the existing TV device."""
    if (device_info := entry.data[ATTR_DEVICE_INFO]) is None:
        return
    async_add_entities(
        PanasonicVieraButton(entry, device_info[ATTR_UDN], description)
        for description in BUTTONS
    )


class PanasonicVieraButton(ButtonEntity):
    """A stateless key using the existing TV connection."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: PanasonicButtonDescription

    def __init__(
        self,
        entry: PanasonicVieraConfigEntry,
        udn: str,
        description: PanasonicButtonDescription,
    ) -> None:
        """Initialize a stable device-owned key."""
        self.entity_description = description
        self._remote = entry.runtime_data
        self._attr_unique_id = f"{udn}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, udn)}, name=entry.data[CONF_NAME]
        )

    @property
    @override
    def available(self) -> bool:
        """Navigation requires an awake TV, unlike its guarded power action."""
        return (
            super().available
            and self._remote.available
            and self._remote.state is MediaPlayerState.ON
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Follow shared availability without an additional polling loop."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._remote.async_add_state_listener(self.async_write_ha_state)
        )

    @override
    async def async_press(self) -> None:
        """Send one key without turning on the TV."""
        await self._remote.async_send_control_key(self.entity_description.command)
