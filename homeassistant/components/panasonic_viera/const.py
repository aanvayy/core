"""Constants for the Panasonic Viera integration."""

from panasonic_viera import Keys

DOMAIN = "panasonic_viera"

DEVICE_MANUFACTURER = "Panasonic"

CONF_ON_ACTION = "turn_on_action"
CONF_INPUT_SOURCES = "input_sources"
CONF_APP_ID = "app_id"
CONF_ENCRYPTION_KEY = "encryption_key"

DEFAULT_NAME = "Panasonic Viera TV"
DEFAULT_PORT = 55000

ATTR_REMOTE = "remote"

ATTR_DEVICE_INFO = "device_info"
ATTR_FRIENDLY_NAME = "friendlyName"
ATTR_MODEL_NUMBER = "modelNumber"
ATTR_UDN = "UDN"

DEFAULT_MANUFACTURER = "Panasonic"
DEFAULT_MODEL_NUMBER = "Panasonic Viera"

ERROR_INVALID_PIN_CODE = "invalid_pin_code"

INPUT_COMMANDS: dict[str, Keys] = {
    "TV": Keys.TV,
    "HDMI1": Keys.HMDI_1,
    "HDMI2": Keys.HMDI_2,
    "HDMI3": Keys.HMDI_3,
    "HDMI4": Keys.HMDI_4,
}
