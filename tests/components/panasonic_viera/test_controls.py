"""Test named legacy inputs and device-owned navigation through HA actions."""

from unittest.mock import AsyncMock, Mock
from urllib.error import URLError

from panasonic_viera import Keys, SOAPError
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_load_fixture

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    MediaPlayerEntityFeature,
)
from homeassistant.components.panasonic_viera.const import (
    ATTR_DEVICE_INFO,
    ATTR_UDN,
    CONF_INPUT_SOURCES,
    CONF_ON_ACTION,
    DOMAIN,
)
from homeassistant.const import ATTR_ASSUMED_STATE, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import parse_yaml

from .conftest import MOCK_CONFIG_DATA, MOCK_DEVICE_INFO

PLAYER = "media_player.panasonic_viera_tv"
SOURCES = {"Apple TV": "HDMI2", "Cast": "HDMI3"}


@pytest.fixture
async def legacy_entry(hass: HomeAssistant, mock_remote: Mock) -> MockConfigEntry:
    """Set up explicit sources on a TV without PAC input discovery."""
    mock_remote.list_inputs.side_effect = SOAPError()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_DEVICE_INFO[ATTR_UDN],
        data={**MOCK_CONFIG_DATA, ATTR_DEVICE_INFO: MOCK_DEVICE_INFO},
        options={CONF_INPUT_SOURCES: SOURCES},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("legacy_entry")
async def test_named_inputs(hass: HomeAssistant, mock_remote: Mock) -> None:
    """Expose real command choices without inventing initial input feedback."""
    state = hass.states.get(PLAYER)
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == list(SOURCES)
    assert ATTR_INPUT_SOURCE not in state.attributes
    assert state.attributes[ATTR_ASSUMED_STATE]
    assert (
        state.attributes[ATTR_SUPPORTED_FEATURES]
        & MediaPlayerEntityFeature.SELECT_SOURCE
    )
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": PLAYER, "source": "Cast"},
        blocking=True,
    )
    mock_remote.send_key.assert_called_once_with(Keys.HMDI_3.value)
    mock_remote.set_input.assert_not_called()
    mock_remote.list_inputs.assert_not_called()
    mock_remote.get_input.assert_not_called()
    assert hass.states.get(PLAYER).attributes[ATTR_INPUT_SOURCE] == "Cast"


@pytest.mark.usefixtures("legacy_entry")
@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("up", Keys.UP),
        ("down", Keys.DOWN),
        ("left", Keys.LEFT),
        ("right", Keys.RIGHT),
        ("ok", Keys.ENTER),
        ("back", Keys.BACK),
        ("menu", Keys.MENU),
        ("input", Keys.INPUT_KEY),
    ],
)
async def test_navigation_buttons(
    hass: HomeAssistant,
    mock_remote: Mock,
    name: str,
    key: Keys,
    entity_registry: er.EntityRegistry,
) -> None:
    """Each native key sends one command and belongs to the original TV."""
    entity_id = f"button.panasonic_viera_tv_{name}"
    registry = entity_registry
    assert (
        registry.async_get(entity_id).device_id == registry.async_get(PLAYER).device_id
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    mock_remote.send_key.assert_called_once_with(key.value)
    assert hass.states.get(entity_id).state not in ("unknown", "unavailable")


@pytest.mark.usefixtures("legacy_entry")
async def test_unknown_input_sends_nothing(
    hass: HomeAssistant, mock_remote: Mock
) -> None:
    """Reject a label outside the configured sources."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": PLAYER, "source": "POWER"},
            blocking=True,
        )
    mock_remote.send_key.assert_not_called()


@pytest.mark.usefixtures("legacy_entry")
async def test_failed_input_clears_assumption(
    hass: HomeAssistant, mock_remote: Mock
) -> None:
    """A failed command must not be presented as a successful input change."""
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": PLAYER, "source": "Apple TV"},
        blocking=True,
    )
    mock_remote.send_key.reset_mock()
    mock_remote.send_key.side_effect = URLError("offline")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": PLAYER, "source": "Cast"},
            blocking=True,
        )
    mock_remote.send_key.assert_called_once_with(Keys.HMDI_3.value)
    assert ATTR_INPUT_SOURCE not in hass.states.get(PLAYER).attributes
    assert hass.states.get("button.panasonic_viera_tv_up").state == "unavailable"


async def test_off_input_never_wakes_tv(hass: HomeAssistant, mock_remote: Mock) -> None:
    """Input selection cannot invoke the retained IR-backed wake hook."""
    wake = AsyncMock()
    hass.services.async_register("test", "wake", wake)
    mock_remote.get_mute.side_effect = SOAPError()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_DEVICE_INFO[ATTR_UDN],
        data={
            **MOCK_CONFIG_DATA,
            ATTR_DEVICE_INFO: MOCK_DEVICE_INFO,
            CONF_ON_ACTION: [{"action": "test.wake"}],
        },
        options={CONF_INPUT_SOURCES: SOURCES},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == "off"
    assert hass.states.get(PLAYER).attributes[ATTR_INPUT_SOURCE_LIST] == list(SOURCES)
    assert hass.states.get("button.panasonic_viera_tv_up").state == "unavailable"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": PLAYER, "source": "Cast"},
            blocking=True,
        )
    mock_remote.send_key.assert_not_called()
    wake.assert_not_called()


async def test_reload_keeps_controls(
    hass: HomeAssistant,
    mock_remote: Mock,
    legacy_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A reload retains button identities/device and resets unverified input state."""
    registry = entity_registry
    before = registry.async_get("button.panasonic_viera_tv_up")
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": PLAYER, "source": "Cast"},
        blocking=True,
    )
    mock_remote.send_key.reset_mock()
    assert await hass.config_entries.async_reload(legacy_entry.entry_id)
    await hass.async_block_till_done()
    after = registry.async_get("button.panasonic_viera_tv_up")
    assert after.id == before.id
    assert after.device_id == before.device_id
    assert (
        len(
            [
                e
                for e in er.async_entries_for_config_entry(
                    registry, legacy_entry.entry_id
                )
                if e.domain == "button"
            ]
        )
        == 8
    )
    assert ATTR_INPUT_SOURCE not in hass.states.get(PLAYER).attributes
    mock_remote.send_key.assert_not_called()


@pytest.mark.parametrize(
    "mapping",
    [
        {"Power": "POWER"},
        {"": "HDMI2"},
        {"   ": "HDMI2"},
        {"Input": "NRC_POWER-ONOFF"},
        [],
    ],
)
async def test_invalid_source_options(
    hass: HomeAssistant, legacy_entry: MockConfigEntry, mapping: object
) -> None:
    """Only discrete TV/HDMI commands may be configured as named inputs."""
    flow = await hass.config_entries.options.async_init(legacy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input={CONF_INPUT_SOURCES: mapping}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_input_sources"}
    assert legacy_entry.options[CONF_INPUT_SOURCES] == SOURCES


async def test_options_can_remove_fallback(
    hass: HomeAssistant, legacy_entry: MockConfigEntry, mock_remote: Mock
) -> None:
    """Removing the mapping restores unmodified native input discovery."""
    mock_remote.list_inputs.side_effect = None
    flow = await hass.config_entries.options.async_init(legacy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input={}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert not hass.states.get(PLAYER).attributes.get(ATTR_ASSUMED_STATE)
    assert hass.states.get(PLAYER).attributes[ATTR_INPUT_SOURCE_LIST] == [
        "TV",
        "HDMI1",
        "HDMI2",
    ]
    mock_remote.list_inputs.assert_called_once()


@pytest.mark.usefixtures("legacy_entry")
async def test_navigation_availability_recovers(
    hass: HomeAssistant, mock_remote: Mock
) -> None:
    """Keys follow network recovery through the existing media poll only."""
    assert await async_setup_component(hass, "homeassistant", {})
    mock_remote.get_mute.side_effect = URLError("offline")
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": PLAYER}, blocking=True
    )
    assert hass.states.get("button.panasonic_viera_tv_up").state == "unavailable"
    mock_remote.get_mute.side_effect = None
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": PLAYER}, blocking=True
    )
    assert hass.states.get("button.panasonic_viera_tv_up").state == "unknown"
    mock_remote.send_key.assert_not_called()


@pytest.fixture
async def navigation_bridge(hass: HomeAssistant, legacy_entry: MockConfigEntry) -> None:
    """Load the standard event-routing automation used with stock HomeKit."""
    config = parse_yaml(
        await async_load_fixture(hass, "homekit_navigation.yaml", DOMAIN)
    )
    assert await async_setup_component(hass, "automation", config)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("navigation_bridge")
@pytest.mark.parametrize(
    ("key", "command"),
    [
        ("arrow_up", Keys.UP),
        ("arrow_down", Keys.DOWN),
        ("arrow_left", Keys.LEFT),
        ("arrow_right", Keys.RIGHT),
        ("select", Keys.ENTER),
        ("back", Keys.BACK),
        ("exit", Keys.EXIT),
        ("information", Keys.INFO),
    ],
)
async def test_homekit_navigation_route(
    hass: HomeAssistant, mock_remote: Mock, key: str, command: Keys
) -> None:
    """HomeKit keys reach the same physical device without hardcoded entity IDs."""
    hass.bus.async_fire(
        "homekit_tv_remote_key_pressed", {"entity_id": PLAYER, "key_name": key}
    )
    await hass.async_block_till_done()
    mock_remote.send_key.assert_called_once_with(command)


@pytest.mark.usefixtures("navigation_bridge")
@pytest.mark.parametrize(
    ("entity", "key"),
    [(PLAYER, "power"), (PLAYER, "play_pause"), ("media_player.other_tv", "arrow_up")],
)
async def test_homekit_navigation_ignores_other_controls(
    hass: HomeAssistant, mock_remote: Mock, entity: str, key: str
) -> None:
    """Unrelated TVs, power and transport keys are never rerouted."""
    hass.bus.async_fire(
        "homekit_tv_remote_key_pressed", {"entity_id": entity, "key_name": key}
    )
    await hass.async_block_till_done()
    mock_remote.send_key.assert_not_called()
