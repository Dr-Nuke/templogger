
import os
import sys
import struct
import time
import pandas as pd
import serial
from typing import Callable, Dict, Any, List
import getpass

from loguru import logger
from pathlib import Path
from dataclasses import dataclass

from templogger.utils import sensor_data_logging

ROOT = Path.cwd()


DATA_DIR = ROOT / "data"
DATA_DIR_SHT = DATA_DIR / "sht"
DATA_DIR_CO2 = DATA_DIR / "co2"
DATA_DIR_SHT_HOURLY = DATA_DIR / "sht_hourly"
DATA_DIR_SHT_DAILY = DATA_DIR / "sht_daily"
DATA_DIR_CO2_HOURLY = DATA_DIR / "co2_hourly"
DATA_DIR_CO2_DAILY = DATA_DIR / "co2_daily"

# Logging
# Remove the default logger
logger.remove()
LOG_FILE_PATH = ROOT / "logs/log.log"

# Configure logger: console output with custom format
logger.add(sys.stdout,
           colorize=True,
           level="DEBUG",
           )
# Optional: log to a file as well
logger.add(LOG_FILE_PATH, rotation="5000 KB", level="DEBUG", enqueue=True)

# Export the configured logger
__all__ = ["logger"]

# OS stuff
OS = sys.platform  # 'linux' or 'win32'
logger.info(
    f"running on platform {OS}, cwd {os.getcwd()}, user {getpass.getuser()} and interpreter {sys.executable}")


# Sensor & collector configs
MAX_RETRIES = 3
SENSOR_TIMEOUT = 20

CHARACTERISTICS: Dict[str, tuple[str, Callable[[bytes], Any]]] = {
    "Battery": ("00002a19-0000-1000-8000-00805f9b34fb", lambda b: int.from_bytes(b, "little")),
    "Humidity": ("00001235-b38d-4985-720e-0f993a68ee41", lambda b: round(struct.unpack("<f", b)[0], 2)),
    "Temperature": ("00002235-b38d-4985-720e-0f993a68ee41", lambda b: round(struct.unpack("<f", b)[0], 2)),
}

# Bluetooth adapters. Find yours with `hciconfig -a` and check the "Bus:"
# field: USB dongles show "Bus: USB", the onboard chip shows "Bus: UART".
# Don't assume the onboard chip's index by default (e.g. hci0) — its
# enumeration order isn't guaranteed. The list below is just an example;
# adjust it to match your own hardware.
ADAPTERS = [
    "hci0",
    "hci1",
    "hci2",
    "hci3",
    "hci4",
]


# Define sensors (skip ID 3 to test robustness)
# === SENSOR DEFINITION ===
@dataclass(frozen=True)
class Sensor:
    mac: str
    name: str


SENSORS_SHT: List[Sensor] = [
    Sensor(mac="FF:FF:FF:FF:FF:01", name="Room1"),
    Sensor(mac="FF:FF:FF:FF:FF:02", name="Room2"),
]

MAX_NAME_LENGTH = max([len(s.name) for s in SENSORS_SHT])
MAX_CHAR_LENGTH = max([len(c) for c in CHARACTERISTICS.keys()])


# Redis DB
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_LAST_TEIMESTAMP_KEY = "dashboard:last_timestamp"


METRICS_SHT = ['Temperature', 'Humidity', 'Battery', 'attempts', 'duration']
METRICS_CO2 = ["CO2"]
METRICS_PLOT = ["Temperature", "CO2", 'Humidity', 'Battery']


AGGREGATIONS = {
    'raw': {
        'retention': 2 * 86400 * 1000,
        'duplicate_policy': "FIRST"},      # 48 hours in ms
    'hourly': {
        'retention': 10 * 86400 * 1000,
        'duplicate_policy': "LAST"},   # 10 days
    'daily': {
        'retention': 365 * 86400 * 1000,
        'duplicate_policy': "LAST"},  # 1 year
}


SENSORS_CO2 = [
    {"location": "hallway", "device": "/dev/ttyAMA0"}
]

# Metrics eligible for aggregation (plot metrics only, not operational metadata)
METRICS_AGG_SHT = ["Temperature", "Humidity", "Battery"]
METRICS_AGG_CO2 = ["CO2"]

# Sensor type configurations for aggregation
SENSOR_TYPES = {
    "sht": {
        "data_dir": DATA_DIR_SHT,
        "hourly_dir": DATA_DIR_SHT_HOURLY,
        "daily_dir": DATA_DIR_SHT_DAILY,
        "prefix": "sht",
        "metrics": METRICS_AGG_SHT,
        "locations": [s.name for s in SENSORS_SHT],
        "value_col_map": None,
    },
    "co2": {
        "data_dir": DATA_DIR_CO2,
        "hourly_dir": DATA_DIR_CO2_HOURLY,
        "daily_dir": DATA_DIR_CO2_DAILY,
        "prefix": "co2",
        "metrics": METRICS_AGG_CO2,
        "locations": [s["location"] for s in SENSORS_CO2],
        "value_col_map": {"co2": "CO2"},
    },
}

# Derived metrics — computed from raw sensor data, stored as time series in Redis
DERIVED_METRICS = [
    {
        "name": "TempDelta",
        "label": "Temp In-Out",
        "unit": "°C",
        "indoor_sensors": ["Room1"],
        "outdoor_sensors": ["Room2"],
        "source_metric": "Temperature",
        "func": "indoor_outdoor_delta",
    },
]

# notification configs
NTFY_CONFIG = {
    "ntfy_topic": "your-ntfy-topic-here",
    "ntfy_battery_minimum": 25,
    "ntfy_minimum_success_fracion": 0.7,
    "ntfy_offline_minuts": 120,
    "email_from": "your-email@example.com",
    "email_password": "your-email-password",
    "email_smtp": "smtp.example.com",
    "email_port": 587,
    "email_to": "recipient@example.com",
    "expected_data_points_24h": 100,  # 70% of 144=6*24
}
