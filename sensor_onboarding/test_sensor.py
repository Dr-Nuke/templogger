#!/usr/bin/env python3
"""
Standalone BLE sensor test script.

For each given MAC address:
  1. Connect (with timeout)
  2. Dump all discovered GATT services and characteristics
  3. Identify which project UUIDs are present on the device
  4. Attempt direct READ on every readable characteristic and decode sensibly

Usage:
    python sensor_onboarding/test_sensor.py <MAC> [<MAC> ...]
    python sensor_onboarding/test_sensor.py --adapter hci1 C6:D5:00:27:E8:80

Run from the repo root (templogger/).
"""

import argparse
import asyncio
import datetime
import struct
import sys
from pathlib import Path

from bleak import BleakClient
from bleak.exc import BleakDeviceNotFoundError
from loguru import logger

# ── Logging setup ────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).resolve().parent / "test_logs"
LOG_DIR.mkdir(exist_ok=True)
_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = LOG_DIR / f"test_logs_{_ts}.log"

logger.remove()
logger.add(sys.stdout, colorize=True, level="DEBUG",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add(LOG_FILE, level="DEBUG",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")

# ── Known UUIDs ───────────────────────────────────────────────────────────────
# Project UUIDs (from config.py)
PROJECT_UUIDS: dict[str, str] = {
    "Battery":           "00002a19-0000-1000-8000-00805f9b34fb",
    "Humidity":          "00001235-b38d-4985-720e-0f993a68ee41",
    "Temperature":       "00002235-b38d-4985-720e-0f993a68ee41",
}

# Additional standard BLE Device Information characteristics worth reading
INFO_UUIDS: dict[str, str] = {
    "Manufacturer Name": "00002a29-0000-1000-8000-00805f9b34fb",
    "Model Number":      "00002a24-0000-1000-8000-00805f9b34fb",
    "Serial Number":     "00002a25-0000-1000-8000-00805f9b34fb",
    "Hardware Rev":      "00002a27-0000-1000-8000-00805f9b34fb",
    "Firmware Rev":      "00002a26-0000-1000-8000-00805f9b34fb",
    "Software Rev":      "00002a28-0000-1000-8000-00805f9b34fb",
}


# ── Decoders ─────────────────────────────────────────────────────────────────

def try_decode(uuid: str, raw: bytes) -> str:
    """Best-effort decode of a characteristic value into a human-readable string."""
    uuid = uuid.lower()
    try:
        # Battery Level — 1 byte, 0–100
        if uuid == "00002a19-0000-1000-8000-00805f9b34fb":
            return f"{int.from_bytes(raw, 'little')} %"
        # SHT3x Humidity / Temperature — 4-byte little-endian float
        if uuid in ("00001235-b38d-4985-720e-0f993a68ee41",
                    "00002235-b38d-4985-720e-0f993a68ee41"):
            if len(raw) == 4:
                return f"{struct.unpack('<f', raw)[0]:.2f}"
            return f"(unexpected length {len(raw)})"
        # Standard string characteristics (Device Information service)
        if uuid in {v.lower() for v in INFO_UUIDS.values()}:
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"(decode error: {e})"
    # Fallback: hex + printable ASCII attempt
    try:
        text = raw.decode("utf-8", errors="replace")
        if all(0x20 <= c < 0x7F or c == 0 for c in raw):
            return f"{raw.hex()}  →  \"{text}\""
    except Exception:
        pass
    return raw.hex()


# ── Core test ────────────────────────────────────────────────────────────────

async def test_one(mac: str, adapter: str) -> None:
    sep = "═" * 60
    logger.info(sep)
    logger.info(f"SENSOR TEST  MAC={mac}  adapter={adapter}")
    logger.info(sep)

    # ── Step 1: Connect (up to MAX_RETRIES attempts) ─────────────────────────
    MAX_RETRIES = 3
    client = None
    connected = False

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"STEP 1  Connecting … (attempt {attempt}/{MAX_RETRIES})")
        client = BleakClient(mac, adapter=adapter)  # fresh instance each attempt
        try:
            await asyncio.wait_for(client.connect(timeout=20), timeout=21)
            if client.is_connected:
                connected = True
                break
            logger.warning(f"  Attempt {attempt}: connect() returned but is_connected=False")
        except asyncio.TimeoutError:
            logger.warning(f"  Attempt {attempt}: timed out after 20 s")
        except BleakDeviceNotFoundError:
            logger.warning(f"  Attempt {attempt}: device not found")
        except Exception as e:
            logger.warning(f"  Attempt {attempt}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(2)

    if not connected:
        logger.error(f"  Failed to connect after {MAX_RETRIES} attempts — aborting")
        return

    logger.success("  Connected!")
    await asyncio.sleep(0.5)  # allow service discovery to settle

    try:
        # ── Step 2: GATT dump ─────────────────────────────────────────────────
        logger.info("")
        logger.info("STEP 2  GATT service / characteristic discovery")
        all_char_uuids: set[str] = set()

        for svc in client.services:
            logger.info(f"  SERVICE  {svc.uuid}  ({svc.description or '—'})")
            for char in svc.characteristics:
                props = ", ".join(char.properties)
                logger.info(f"    CHAR  {char.uuid}  [{props}]  ({char.description or '—'})")
                all_char_uuids.add(char.uuid.lower())
                for desc in char.descriptors:
                    logger.info(f"      DESC  {desc.uuid}  ({desc.description or '—'})")

        # ── Step 3: UUID presence check ───────────────────────────────────────
        logger.info("")
        logger.info("STEP 3  Project UUID presence on this device")
        for label, uuid in {**PROJECT_UUIDS, **INFO_UUIDS}.items():
            present = uuid.lower() in all_char_uuids
            marker = "✔" if present else "✘"
            logger.info(f"  {marker}  {label:<20}  {uuid}")

        # ── Step 4: Read all readable characteristics ─────────────────────────
        logger.info("")
        logger.info("STEP 4  Reading every readable characteristic")

        readable_chars: list[tuple[str, str]] = []
        for svc in client.services:
            for char in svc.characteristics:
                if "read" in char.properties:
                    readable_chars.append((char.uuid, char.description or ""))

        if not readable_chars:
            logger.warning("  No readable characteristics found")
        for uuid, desc in readable_chars:
            try:
                raw = await client.read_gatt_char(uuid)
                decoded = try_decode(uuid, raw)
                logger.info(f"  READ  {uuid}  ({desc or '—'})")
                logger.info(f"        hex={raw.hex()}  →  {decoded}")
            except Exception as e:
                logger.warning(f"  READ  {uuid}  ({desc or '—'})  FAILED: {e}")

    finally:
        if client is not None:
            try:
                await client.disconnect()
                logger.info("")
                logger.info("Disconnected cleanly.")
            except Exception as e:
                logger.warning(f"Disconnect error (ignored): {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(macs: list[str], adapter: str) -> None:
    for mac in macs:
        await test_one(mac, adapter)
        if len(macs) > 1:
            logger.info("Waiting 2 s before next sensor …")
            await asyncio.sleep(2)

    logger.info("")
    logger.info(f"Test log written to: {LOG_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test BLE SHT sensor GATT connectivity and characteristic access."
    )
    parser.add_argument("macs", nargs="+", metavar="MAC",
                        help="Bluetooth MAC address(es) to test, e.g. C6:D5:00:27:E8:80")
    parser.add_argument("--adapter", default="hci0", metavar="HCI",
                        help="Bluetooth adapter to use (default: hci0)")
    args = parser.parse_args()

    logger.info(f"Log file: {LOG_FILE}")
    asyncio.run(run(args.macs, args.adapter))


if __name__ == "__main__":
    main()
