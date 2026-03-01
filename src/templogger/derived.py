"""
Derived metrics computation.
Called from run_all_sensors.sh after collector.py, before aggregate.py.
Reads latest raw sensor values from Redis, computes derived metrics,
and writes results back to Redis as time series.

Usage: python src/templogger/derived.py
"""

import redis
import numpy as np

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


def _apparent_temp(T, RH, ws):
    """Australian Apparent Temperature (Steadman 1994).

    T:   air temperature in °C
    RH:  relative humidity in %
    ws:  wind speed in m/s at 10 m height
    Returns AT in °C.
    """
    e = (RH / 100.0) * 6.105 * np.exp(17.27 * T / (237.7 + T))
    return T + 0.33 * e - 0.70 * ws - 4.00


def _poly_smooth(times_ms, values, t_ms, degree):
    """Fit a degree-N polynomial to (times_ms, values) centred at t_ms.

    Returns the evaluated value at t_ms, or None if insufficient points.
    Timestamps are normalised to minutes relative to t_ms for numerical stability.
    """
    if len(times_ms) < degree + 1:
        return None
    t_arr = np.array(times_ms, dtype=float)
    t_norm = (t_arr - float(t_ms)) / 60000.0  # minutes relative to evaluation point
    v_arr = np.array(values, dtype=float)
    coeffs = np.polyfit(t_norm, v_arr, degree)
    return float(np.polyval(coeffs, 0.0))  # evaluate at t_ms (normalised = 0)


def compute_poly_smooth_temps(r, metric_def, timestamp_ms):
    """Compute polynomial-smoothed indoor mean and outdoor min temperatures.

    Fetches the last window_min minutes of raw data for each sensor, fits a
    polynomial of the configured degree, and evaluates at timestamp_ms.

    Returns a dict {"PolyTempIndoor": val, "PolyTempOutdoor": val},
    or None if there is insufficient data for either group.
    """
    ts = r.ts()
    source_metric = metric_def["source_metric"]
    window_ms = metric_def.get("window_min", 120) * 60 * 1000
    degree = metric_def.get("degree", 2)
    start_ms = timestamp_ms - window_ms

    indoor_smoothed = []
    indoor_raw = []
    for sensor in metric_def["indoor_sensors"]:
        key = make_key(sensor, source_metric, "raw")
        try:
            data = ts.range(key, start_ms, timestamp_ms)
        except redis.ResponseError:
            continue
        if not data:
            continue
        indoor_raw.append(data[-1][1])  # most recent value in window
        if len(data) < degree + 1:
            continue
        times_list = [d[0] for d in data]
        vals = [d[1] for d in data]
        v = _poly_smooth(times_list, vals, timestamp_ms, degree)
        if v is not None:
            indoor_smoothed.append(v)

    outdoor_smoothed = []
    outdoor_raw = []
    for sensor in metric_def["outdoor_sensors"]:
        key = make_key(sensor, source_metric, "raw")
        try:
            data = ts.range(key, start_ms, timestamp_ms)
        except redis.ResponseError:
            continue
        if not data:
            continue
        outdoor_raw.append(data[-1][1])  # most recent value in window
        if len(data) < degree + 1:
            continue
        times_list = [d[0] for d in data]
        vals = [d[1] for d in data]
        v = _poly_smooth(times_list, vals, timestamp_ms, degree)
        if v is not None:
            outdoor_smoothed.append(v)

    if not indoor_smoothed or not outdoor_smoothed:
        return None

    result = {
        "PolyTempIndoor": round(sum(indoor_smoothed) / len(indoor_smoothed), 2),
        "PolyTempOutdoor": round(min(outdoor_smoothed), 2),
    }
    if indoor_raw:
        result["RawIndoorMean"] = round(sum(indoor_raw) / len(indoor_raw), 2)
    if outdoor_raw:
        result["RawOutdoor"] = round(min(outdoor_raw), 2)

    # Apparent temperature — fetch current humidity via ts.get() (single point)
    indoor_rh = []
    for sensor in metric_def["indoor_sensors"]:
        hkey = make_key(sensor, "Humidity", "raw")
        try:
            hrec = ts.get(hkey)
        except redis.ResponseError:
            continue
        if hrec is not None:
            indoor_rh.append(hrec[1])

    outdoor_rh = []
    for sensor in metric_def["outdoor_sensors"]:
        hkey = make_key(sensor, "Humidity", "raw")
        try:
            hrec = ts.get(hkey)
        except redis.ResponseError:
            continue
        if hrec is not None:
            outdoor_rh.append(hrec[1])

    if indoor_raw and indoor_rh:
        result["ApparentTempIndoor"] = round(
            _apparent_temp(
                sum(indoor_raw) / len(indoor_raw),
                sum(indoor_rh) / len(indoor_rh),
                0.0,
            ), 2)

    if outdoor_raw and outdoor_rh:
        result["ApparentTempVent"] = round(
            _apparent_temp(min(outdoor_raw), min(outdoor_rh), 1.0), 2)

    return result


# Registry mapping func names (from config) to callables
FUNC_REGISTRY = {
    "indoor_outdoor_delta": compute_indoor_outdoor_delta,
    "poly_smooth_temps": compute_poly_smooth_temps,
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

        if isinstance(value, dict):
            # Multi-output metric: write each series to its own Redis key
            for series_def in metric_def.get("series", []):
                series_key_name = series_def["key"]
                series_val = value.get(series_key_name)
                if series_val is None:
                    continue
                key = make_key("derived", series_key_name, "raw")
                try:
                    ts.add(key, timestamp_ms, float(series_val))
                    logger.info(f"derived {series_key_name}: {series_val}")
                except redis.ResponseError as e:
                    if "TSDB: Timestamp is older than retention" not in str(e):
                        raise
                    logger.info(f"derived {series_key_name}: skipped (older than retention)")
        else:
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
