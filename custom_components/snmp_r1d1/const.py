"""Constants for snmp_r1d1 integration."""

from .device_loader import load_devices

DOMAIN = "snmp_r1d1"

# Polling Settings
DEFAULT_POLLING_INTERVAL = 30
MIN_POLLING_INTERVAL = 10
MAX_POLLING_INTERVAL = 300
DEFAULT_MAC_UPDATE_CYCLE = 5
MAX_MAC_UPDATE_CYCLE = 30
SLOW_UPDATE_CYCLE = 60

# Performance Limits
MAX_PORTS = 50
MAX_CUSTOM_OIDS = 10

# SNMP Options
SNMP_VERSIONS = ["v1", "v2c", "v3"]
AUTH_PROTOCOLS = ["SHA", "MD5", "None"]
PRIVACY_PROTOCOLS = ["AES", "DES", "None"]


# Configuration Keys
CONF_DEVICE_IP = "device_ip"
CONF_DEVICE_NAME = "device_name"
CONF_ENTITY_PREFIX = "entity_prefix"
CONF_DEVICE_TYPE = "device_type"
CONF_SNMP_VERSION = "snmp_version"
CONF_ENABLE_CONTROLS = "enable_controls"
CONF_READ_COMMUNITY_STRING = "read_community_string"
CONF_WRITE_COMMUNITY_STRING = "write_community_string"
CONF_USERNAME = "username"
CONF_AUTH_PROTOCOL = "auth_protocol"
CONF_AUTH_KEY = "auth_key"
CONF_PRIVACY_PROTOCOL = "privacy_protocol"
CONF_PRIVACY_KEY = "privacy_key"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_MAC_UPDATE_CYCLE = "mac_update_cycle"
CONF_CUSTOM_OIDS = "custom_oids"
CONF_VALIDATED_OIDS = "validated_oids"
CONF_DEVICE_INFO = "device_info"
CONF_CONFIG_SUMMARY = "config_summary"
CONF_GO_BACK = "go_back"
CONF_CONFIRM = "confirm"
CONF_ENTITIES = "entities"

DEVICE_TYPE_OIDS = load_devices()
