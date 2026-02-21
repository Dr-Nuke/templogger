"""
Derived metrics computation.
Called from run_all_sensors.sh after collector.py, before aggregate.py.
Reads latest raw sensor values from Redis, computes derived metrics,
and writes results back to Redis as time series.

Usage: python src/templogger/derived.py
"""

import redis

from templogger.config import DERIVED_METRICS, SENSORS_SHT, logger
from templogger.utils import get_redis, make_key


def compute_indoor_outdoor_delta(r, metric_def, timestamp_ms):
    """Compute mean(indoor temperatures) - min(outdoor temperatures).

    Returns the delta value, or None if outdoor data is unavailable.
    """
    ts = r.ts()
    source_metric = metric_def["source_metric"]

    # collect indoor values at the current timestamp
    indoor_values = []
    for sensor in metric_def["indoor_sensors"]:
        key = make_key(sensor, source_metric, "raw")
        try:
            result = ts.get(key)
        except redis.ResponseError:
            continue
        if result is None:
            continue
        ts_ms, val = result
        if ts_ms == timestamp_ms:
            indoor_values.append(val)

    # collect outdoor values at the current timestamp
    outdoor_values = []
    for sensor in metric_def["outdoor_sensors"]:
        key = make_key(sensor, source_metric, "raw")
        try:
            result = ts.get(key)
        except redis.ResponseError:
            continue
        if result is None:
            continue
        ts_ms, val = result
        if ts_ms == timestamp_ms:
            outdoor_values.append(val)

    if not outdoor_values or not indoor_values:
        return None

    indoor_mean = sum(indoor_values) / len(indoor_values)
    outdoor_val = min(outdoor_values)
    return round(indoor_mean - outdoor_val, 2)


# Registry mapping func names (from config) to callables
FUNC_REGISTRY = {
    "indoor_outdoor_delta": compute_indoor_outdoor_delta,
}


def compute_derived_for_timestamp(r, timestamp_ms):
    """Compute all derived metrics for a given timestamp and write to Redis."""
    ts = r.ts()
    for metric_def in DERIVED_METRICS:
        func_name = metric_def["func"]
        func = FUNC_REGISTRY.get(func_name)
        if func is None:
            logger.warning(f"unknown derived func: {func_name}")
            continue

        value = func(r, metric_def, timestamp_ms)
        if value is None:
            logger.info(f"derived {metric_def['name']}: skipped (missing data)")
            continue

        key = make_key("derived", metric_def["name"], "raw")
        try:
            ts.add(key, timestamp_ms, float(value))
            logger.info(f"derived {metric_def['name']}: {value}")
        except redis.ResponseError as e:
            if "TSDB: Timestamp is older than retention" not in str(e):
                raise
            logger.info(f"derived {metric_def['name']}: skipped (older than retention)")


if __name__ == "__main__":
    logger.info("computing derived metrics")
    r = get_redis()

    # get the latest timestamp from a reference sensor
    ref_key = make_key(SENSORS_SHT[0].name, "Temperature", "raw")
    try:
        result = r.ts().get(ref_key)
    except redis.ResponseError:
        logger.warning(f"reference key {ref_key} not found, skipping derived metrics")
        result = None

    if result is not None:
        timestamp_ms = result[0]
        compute_derived_for_timestamp(r, timestamp_ms)

    logger.info("derived metrics complete")
