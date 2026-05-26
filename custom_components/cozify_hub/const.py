"""Constants for the Cozify HUB integration."""
from datetime import timedelta

DOMAIN = "cozify_hub"
MANUFACTURER = "Cozify"

# API Configuration
API_URLS = {
    "production": "https://api.cozify.fi/ui/0.2",
    "development": "https://testapi.cozify.fi/ui/0.2",
}
API_URLS_FALLBACK = {
    "production": "https://cloud2.cozify.fi/ui/0.2",
}
COZIFY_API_VERSION = "1.14"
COZIFY_LOCAL_API_PORT = 8893

# Connection modes
CONNECTION_MODE_CLOUD = "cloud"
CONNECTION_MODE_LOCAL = "local"

# API environments
API_ENVIRONMENT_PRODUCTION = "production"
API_ENVIRONMENT_DEVELOPMENT = "development"

# Scan intervals
DEFAULT_SCAN_INTERVAL_CLOUD = timedelta(seconds=30)
DEFAULT_SCAN_INTERVAL_LOCAL = timedelta(seconds=10)

# Configuration keys
CONF_CONNECTION_MODE = "connection_mode"
CONF_API_ENVIRONMENT = "api_environment"
CONF_CLOUD_TOKEN = "cloud_token"
CONF_HUB_ID = "hub_id"
CONF_HUB_TOKEN = "hub_token"
CONF_HUB_NAME = "hub_name"
CONF_HUB_HOST = "hub_host"
CONF_EMAIL = "email"

# Platforms
PLATFORMS = [
    "binary_sensor",
    "climate",
    "cover",
    "fan",
    "light",
    "scene",
    "sensor",
    "switch",
    "valve",
]

# Capability → platform mapping
CAPABILITY_PLATFORM_MAP = {
    "TEMPERATURE": "sensor",
    "HUMIDITY": "sensor",
    "PRESSURE": "sensor",
    "LUX": "sensor",
    "CO2": "sensor",
    "VOC": "sensor",
    "ACTIVE_POWER": "sensor",
    "MEASURE_POWER": "sensor",
    "POWER_METER": "sensor",
    "FLOW": "sensor",
    "FLOW_VOLUME": "sensor",
    "FLOW_TEMPERATURE": "sensor",
    "RSSI": "sensor",
    "BATTERY_U": "sensor",
    "BATTERY_C": "sensor",
    "MOTION": "binary_sensor",
    "SELF_MOTION": "binary_sensor",
    "CONTACT": "binary_sensor",
    "SMOKE": "binary_sensor",
    "MOISTURE": "binary_sensor",
    "TWILIGHT": "binary_sensor",
    "LOW_TEMP": "binary_sensor",
    "HIGH_TEMP": "binary_sensor",
    "CO": "binary_sensor",
    "GENERATE_ALERT": "binary_sensor",
    "ON_OFF": "switch",
    "SIGNAL": "switch",
    "BRIGHTNESS": "light",
    "COLOR_HS": "light",
    "COLOR_TEMP": "light",
    "THERMOSTAT": "climate",
    "HVAC": "climate",
    "CONTROL_TEMPERATURE": "climate",
    "AIRCON": "climate",
    "BLINDS": "cover",
    "LIFT": "cover",
    "TILT": "cover",
    "SHUTTER": "cover",
    "VU": "fan",
    "FAN_MODE": "fan",
    "VALVE": "valve",
}
