import datetime
import sys
from statistics import mean
from typing import Any, Dict, Optional, List

import pandas as pd

from templogger.config import (ADAPTERS, CHARACTERISTICS, DATA_DIR_SHT,
                               MAX_CHAR_LENGTH, MAX_NAME_LENGTH, MAX_RETRIES,
                               OS, ROOT, SENSOR_TIMEOUT, SENSORS_SHT, Sensor, NTFY_CONFIG,
                               REDIS_HOST, REDIS_PORT, logger)
from templogger.utils import (df_prep_for_redis, get_redis,
                              push_raw_sht_data_redis, sensor_data_logging,
                              timestamp_now, make_key)
import redis
from email.mime.text import MIMEText
import smtplib

r = get_redis()
ts = r.ts()

def check_offline_sensors():
    issues = []
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=NTFY_CONFIG.get("ntfy_offline_minuts"))
    for sensor in SENSORS_SHT:
        key = make_key(sensor.name, "Temperature", "raw")

        try:
            result = ts.get(key)
        except redis.exceptions.ResponseError:
            result = None

        if not result:
            issues.append(f"{sensor.name}: no data available")
            continue

        ts_ms, _value = result
        last_seen = datetime.datetime.utcfromtimestamp(ts_ms / 1000)

        if last_seen < cutoff:
            issues.append(
                f"{sensor.name}: last seen at {last_seen.isoformat()}, offline > {NTFY_CONFIG.get("ntfy_offline_minuts")} min)")
    return issues

def check_low_battery() -> List[str]:
    issues = []
    battery_min = NTFY_CONFIG.get("ntfy_battery_minimum")
    for sensor in SENSORS_SHT:
        key = make_key(sensor.name, "Battery", "raw")

        try:
            result = ts.get(key)
        except redis.exceptions.ResponseError:
            result = None

        if not result: #already handled in
            continue

        _ts_ms, value = result

        if value < battery_min:
            issues.append(
                f"{sensor.name}: low battery ({value:.2f})"
            )

    return issues

def send_email(subject, body):
    msg = MIMEText(body)
    msg["From"] = NTFY_CONFIG.get("email_from")
    msg["To"] = NTFY_CONFIG.get("email_to")
    msg["Subject"] = subject

    with smtplib.SMTP(NTFY_CONFIG.get("email_smtp"),
                          NTFY_CONFIG.get("email_port")) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(NTFY_CONFIG.get("email_from"), 
                     NTFY_CONFIG.get("email_password"))
        server.send_message(msg)

def check_missing_data() -> List[str]:
    issues = []
    min_points = NTFY_CONFIG.get("expected_data_points_24h")

    now = datetime.datetime.utcnow()
    start_ts = int((now - datetime.timedelta(hours=24)).timestamp() * 1000)  # in ms
    end_ts = int(now.timestamp() * 1000)

    for sensor in SENSORS_SHT:
        key = make_key(sensor.name, "Temperature", "raw")

        try:
            points = ts.range(key, from_time=start_ts, to_time=end_ts)
        except redis.exceptions.ResponseError:
            points = []

        count = len(points)

        if count < min_points:
            issues.append(
                f"{sensor.name}: only {count} data points in last 24h "
            )

    return issues

def main():
    problems = []
    problems += check_offline_sensors()
    problems += check_low_battery()
    problems += check_missing_data()
    logger.info(f"found {len(problems)} problems")
    
    if problems:
        body = "\n".join(problems)
        send_email("Sensor Alert Summary", body)

if __name__ == "__main__":
    main()
