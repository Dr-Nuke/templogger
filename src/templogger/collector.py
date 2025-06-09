import asyncio
import datetime
import sys
import argparse
from statistics import mean
from typing import Dict, Any, Optional
import getpass
import pandas as pd
from bleak import BleakClient
from bleak.exc import BleakDeviceNotFoundError
from loguru import logger

from config import CHARACTERISTICS, ADAPTERS, SENSORS, MAX_NAME_LENGTH, MAX_CHAR_LENGTH, SENSOR_TIMEOUT, MAX_RETRIES, \
    Sensor, OS, DATA_DIR_SHT, ROOT
from utils import sensor_data_logging, timestamp_now


# === Helpers ===
def padded_name(s):
    return s.ljust(MAX_NAME_LENGTH)


def padded_char(c):
    return c.ljust(MAX_CHAR_LENGTH)


# === BLE INTERACTION ===
async def read_characteristics(client: BleakClient, sensor: Sensor) -> Dict[str, Any]:
    name = sensor.name
    sensor_data = {}
    successes = []
    errors = []

    for char_name, (uuid, decoder) in CHARACTERISTICS.items():
        try:
            raw = await client.read_gatt_char(uuid)
            value = decoder(raw)
            logger.info(f"[{padded_name(name)}] {padded_char(char_name)}: {value}")
            sensor_data[char_name] = value
            successes.append(True)
        except Exception as e:
            logger.warning(f"[{padded_name(name)}] Failed to read {char_name}: {e}")
            errors.append(e)
            successes.append(False)
    return all(successes), sensor_data, errors


# === TASK WRAPPER ===
class SensorTask:
    def __init__(self, sensor: Sensor):
        self.sensor = sensor
        self.mac = sensor.mac
        self.name = sensor.name
        self.attempt = 0
        self.success = False
        self.last_error: Optional[str] = None
        self.errors = []
        self.result: Dict[str, Any] = {}
        self.timestamp: Optional[datetime.datetime] = None
        self.duration: Optional[float] = None

    async def run(self, adapter: str):
        self.attempt += 1
        logger.info(f"[{padded_name(self.name)}] Attempt {self.attempt}/{MAX_RETRIES} via {adapter}, connecting...")
        try:
            start = timestamp_now()
            if OS == "linux":
                client = BleakClient(self.mac, adapter=adapter)  # timeout=0.0
            else:
                client = BleakClient(self.mac)
            try:
                await asyncio.wait_for(client.connect(timeout=SENSOR_TIMEOUT), timeout=SENSOR_TIMEOUT + 1)
            except asyncio.TimeoutError as e:
                logger.error(f"[{padded_name(self.name)}] BLE connection timed out after {SENSOR_TIMEOUT}s.")
                self.errors.append(e)
            if client.is_connected:
                logger.info(f"[{padded_name(self.name)}] Connected via {adapter}.")
                success, result, errors = await read_characteristics(client, self.sensor)
                if success:
                    self.success = True
                    self.timestamp = timestamp_now()
                    self.duration = round((timestamp_now() - start).total_seconds(), 1)
                    self.result = result
                else:
                    [self.errors.append(e) for e in errors]
                    await asyncio.sleep(1)  # brief pause before retry
            await client.disconnect()
        except BleakDeviceNotFoundError as e:
            self.last_error = str(e)
            self.errors.append(e)
            logger.error(f"[{padded_name(self.name)}] Failed at attempt {self.attempt} via {adapter}: {e}")
            await asyncio.sleep(1)  # brief pause before retry
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logger.error(
                f"[{padded_name(self.name)}] Encountered unspecified error on {self.attempt} in line {exc_tb.tb_lineno} via {adapter}: {e}")
            self.errors.append(e)
            await asyncio.sleep(1)  # brief pause before retry


# === ASYNC WORKER LOOP ===
async def worker(queue: asyncio.Queue, adapter: str):
    while not queue.empty():
        task: SensorTask = await queue.get()

        if task.success or task.attempt >= MAX_RETRIES:
            queue.task_done()
            continue

        await task.run(adapter)

        if not task.success and task.attempt < MAX_RETRIES:
            await queue.put(task)
        queue.task_done()


# === MAIN EXECUTION ===
async def main() -> Dict[str, Dict[str, Any]]:
    queue = asyncio.Queue()
    task_list = [SensorTask(sensor) for sensor in SENSORS]

    for task in task_list:
        await queue.put(task)

    workers = [asyncio.create_task(worker(queue, adapter)) for adapter in ADAPTERS]
    await queue.join()

    for w in workers:
        w.cancel()

    # Prepare final structured result
    results = {
        task.name: {
            "success": task.success,
            "attempts": task.attempt,
            "errors": task.errors,
            "data": task.result,
            "sensor_time": task.timestamp,
            "duration": task.duration,
        }
        for task in task_list
    }

    logger.info("\n--- Final Results ---")
    for name, result in results.items():
        status = "✅ Success" if result["success"] else "❌ Failed"
        logger.info(f"{padded_name(name)}: {status} after {result['attempts']} attempt(s)")

    return results


def prep_data_for_csv(data):
    df = pd.DataFrame.from_dict(sensor_result)
    df = df.transpose().reset_index()
    df["time"] = timestamp
    df = df.join(pd.json_normalize(df['data']), how="inner")
    df = df.rename(columns={"index": "location"})
    df = df.drop(columns=["success", "data"])
    df = df.set_index("time").reset_index()
    return df


if __name__ == "__main__":
    # get args
    parser = argparse.ArgumentParser()
                    
    parser.add_argument("-s",
                        "--source",
                        help="adds information on who runs the script, i.e. 'cronjob'. appears in the logging",
                        type=str,
                        default=None)

    args = parser.parse_args()
    source = args.source
    if source is not None:
        logger.info(f"running sht sensors by '{getpass.getuser()}' from '{source}' from '{ROOT}' with '{sys.executable}'")
        
    timestamp = timestamp_now()
    sensor_result = asyncio.run(main())

    # results & analytics
    for name, result in sensor_result.items():
        print(padded_name(name), result)
    script_time = round((timestamp_now() - timestamp).total_seconds() / 60, 2)
    logger.info(f"reading {len(SENSORS)} sensors took {script_time} minutes")

    sensor_durations = [r["duration"] for r in sensor_result.values() if r["duration"]]
    if sensor_durations:
        logger.info(f"sensor durations mean: {round(mean(sensor_durations), 2)}, values are {sensor_durations}")


    df = prep_data_for_csv(sensor_result)
    sensor_data_logging(df, "sht", DATA_DIR_SHT, timestamp)

    logger.info("end")
