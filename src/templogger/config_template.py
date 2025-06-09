
import os
import sys
import struct
from typing import Callable, Dict, Any, List

from loguru import logger
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
DATA_DIR_SHT = DATA_DIR / "sht"

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
OS = sys.platform # 'linux' or 'win32'
logger.info(f"running on platform {OS}, cwd {os.getcwd()} and interpreter {sys.executable}")

POLL_INTERVAL = 600  # 10 minutes
REDIS_HOST = "localhost"
REDIS_PORT = 6379

RAW_RETENTION_HOURS = 48
AGG_HOURLY_DAYS = 30
AGG_DAILY_DAYS = 365

# Sensor & collector configs
MAX_RETRIES = 4
SENSOR_TIMEOUT = 30

CHARACTERISTICS: Dict[str, tuple[str, Callable[[bytes], Any]]] = {
    "Battery": ("00002a19-0000-1000-8000-00805f9b34fb", lambda b: int.from_bytes(b, "little")),
    "Humidity": ("00001235-b38d-4985-720e-0f993a68ee41", lambda b: round(struct.unpack("<f", b)[0], 2)),
    "Temperature": ("00002235-b38d-4985-720e-0f993a68ee41", lambda b: round(struct.unpack("<f", b)[0], 2)),
}

# Bluetooth adapters, see hciconfig from cli. default only hci0, the onboard-adapter
ADAPTERS = [
    "hci0",
    # "hci1",
    # "hci2",
    # "hci3",
    # "hci4",
]


# Define sensors (skip ID 3 to test robustness)
# === SENSOR DEFINITION ===
@dataclass(frozen=True)
class Sensor:
    id: int
    mac: str
    name: str


SENSORS: List[Sensor] = [
    Sensor(id=1, mac="FF:FF:FF:FF:FF:FF", name="House"),
    Sensor(id=2, mac="FF:FF:FF:FF:FF:FF", name="Garden"),

]

MAX_NAME_LENGTH = max([len(s.name) for s in SENSORS])
MAX_CHAR_LENGTH = max([len(c) for c in CHARACTERISTICS.keys()])
