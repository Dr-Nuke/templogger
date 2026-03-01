import datetime
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import redis
import seaborn as sns


from templogger.config import (AGGREGATIONS, DATA_DIR_CO2, DATA_DIR_SHT,
                               DERIVED_METRICS, METRICS_CO2, METRICS_SHT,
                               REDIS_LAST_TEIMESTAMP_KEY, SENSORS_CO2,
                               SENSORS_SHT, SENSOR_TYPES, logger)
from templogger.utils import (compute_aggregates, df_prep_for_redis,
                              get_last_aggregated_time, get_redis,
                              load_raw_csvs, make_key, push_aggregate_redis,
                              push_raw_co2_data_redis, push_raw_sht_data_redis,
                              timestamp_now, write_aggregate_csv)


def create_schema(r):
    counter_success = 0
    counter_ignore = 0
    sensor_names = [s.name for s in SENSORS_SHT]
    for location in sensor_names:
        for metric in METRICS_SHT:
            for agg, props in AGGREGATIONS.items():
                key = make_key(location, metric, agg)
                try:
                    r.ts().create(
                        key,
                        retention_msecs=props["retention"],
                        duplicate_policy=props["duplicate_policy"],
                    )
                    logger.info(f"created key {key}")
                    counter_success += 1
                except redis.ResponseError as e:
                    if "TSDB: key already exists" not in str(e):
                        raise
                    logger.info(f"key {key} already exists - skipping")
                    counter_ignore += 1

    for co2_sensor in SENSORS_CO2:
        for metric in METRICS_CO2:
            for agg, props in AGGREGATIONS.items():
                key = make_key(co2_sensor["location"], metric, agg)
                try:
                    r.ts().create(
                        key,
                        retention_msecs=props["retention"],
                        duplicate_policy=props["duplicate_policy"],
                    )
                    logger.info(f"created key {key}")
                    counter_success += 1
                except redis.ResponseError as e:
                    if "TSDB: key already exists" not in str(e):
                        raise
                    logger.info(f"key {key} already exists - skipping")
                    counter_ignore += 1

    for dm in DERIVED_METRICS:
        if "series" in dm:
            keys_to_create = [make_key("derived", s["key"], "raw") for s in dm["series"]]
        else:
            keys_to_create = [make_key("derived", dm["name"], "raw")]
        for key in keys_to_create:
            try:
                r.ts().create(
                    key,
                    retention_msecs=AGGREGATIONS["raw"]["retention"],
                    duplicate_policy="LAST",
                )
                logger.info(f"created key {key}")
                counter_success += 1
            except redis.ResponseError as e:
                if "TSDB: key already exists" not in str(e):
                    raise
                logger.info(f"key {key} already exists - skipping")
                counter_ignore += 1

    try:
        r.set(REDIS_LAST_TEIMESTAMP_KEY, 0)
    except Exception as e:
        logger.info(f"could not set last imestamp value: {e}")

    logger.info(
        f"created {counter_success} key and ignored {counter_ignore} due to already exising")


def ingest_raw(r, data_dir, prefix):
    # get required date ranges or first day to load
    now = timestamp_now()
    timespan = AGGREGATIONS["raw"]["retention"]
    timespan_td = datetime.timedelta(milliseconds=timespan)
    then = (timestamp_now() - timespan_td).date()

    # find all files involved
    fpaths = data_dir.glob(f"{prefix}_data_*.csv")
    fpath_list = list(fpaths)
    read_files = [f for f in fpath_list if datetime.datetime.strptime(
        f.stem[-10:], '%Y-%m-%d').date() >= then]
    logger.info(
        f"reading in files \n{"\n".join([f.name for f in read_files])}")

    # load & adjust schema
    dfs = [pd.read_csv(f) for f in read_files]
    df = pd.concat(dfs).reset_index(drop=True)
    df = df_prep_for_redis(df)

    return df


def ingest_aggregate_csvs(r, sensor_type, agg_level):
    """Load aggregate CSV files within the retention window and push to Redis."""
    retention_ms = AGGREGATIONS[agg_level]["retention"]
    retention_td = datetime.timedelta(milliseconds=retention_ms)
    cutoff_date = (timestamp_now() - retention_td).date()

    agg_dir = sensor_type["hourly_dir"] if agg_level == "hourly" else sensor_type["daily_dir"]
    prefix = sensor_type["prefix"]
    metrics = sensor_type["metrics"]

    pattern = f"{prefix}_{agg_level}_*.csv"
    fpaths = sorted(agg_dir.glob(pattern))

    if not fpaths:
        logger.info(f"no {agg_level} CSV files found in {agg_dir}")
        return

    dfs = []
    for fpath in fpaths:
        try:
            df = pd.read_csv(fpath)
            if df.empty:
                continue
            df["time"] = pd.to_datetime(df["time"])
            df = df[df["time"].dt.date >= cutoff_date]
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            logger.warning(f"skipping {fpath.name}: {e}")

    if not dfs:
        logger.info(f"no {agg_level} data within retention window for {prefix}")
        return

    df_all = pd.concat(dfs, ignore_index=True)
    push_aggregate_redis(r, df_all, metrics, "location", agg_level)
    logger.info(f"ingested {len(df_all)} {agg_level} records from CSV for {prefix}")


def marker_backfill(r, sensor_type):
    """Backfill missing aggregates using the marker-based approach."""
    prefix = sensor_type["prefix"]
    metrics = sensor_type["metrics"]
    value_col_map = sensor_type["value_col_map"]
    now = timestamp_now()

    for agg_level, freq, agg_dir in [
        ("hourly", "h", sensor_type["hourly_dir"]),
        ("daily", "D", sensor_type["daily_dir"]),
    ]:
        retention_ms = AGGREGATIONS[agg_level]["retention"]
        retention_td = datetime.timedelta(milliseconds=retention_ms)

        last_time = get_last_aggregated_time(agg_dir, prefix, agg_level)

        if last_time is not None:
            if agg_level == "hourly":
                start_date = (last_time + datetime.timedelta(hours=1)).date()
            else:
                start_date = (last_time + datetime.timedelta(days=1)).date()
        else:
            # fallback: recompute full retention window
            start_date = (now - retention_td).date()
            logger.info(f"no {agg_level} marker found for {prefix}, "
                        f"backfilling from {start_date}")

        if agg_level == "daily":
            end_date = (now - datetime.timedelta(days=1)).date()
        else:
            end_date = now.date()

        if start_date > end_date:
            logger.info(f"no {agg_level} backfill needed for {prefix}")
            continue

        df_raw = load_raw_csvs(sensor_type["data_dir"], prefix,
                               start_date, end_date, value_col_map)
        if df_raw.empty:
            logger.info(f"no raw data for {prefix} {agg_level} backfill "
                        f"({start_date} to {end_date})")
            continue

        # filter to only completed periods
        if agg_level == "hourly":
            current_hour = now.replace(minute=0, second=0, microsecond=0)
            df_raw = df_raw[df_raw["time"] < current_hour]
        else:
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            df_raw = df_raw[df_raw["time"] < today]

        if df_raw.empty:
            continue

        df_agg = compute_aggregates(df_raw, metrics, "location", freq)
        if df_agg.empty:
            continue

        push_aggregate_redis(r, df_agg, metrics, "location", agg_level)
        write_aggregate_csv(df_agg, agg_dir, prefix, agg_level)
        logger.info(f"marker backfill {prefix} {agg_level}: "
                    f"wrote {len(df_agg)} records ({start_date} to {end_date})")


def _recompute_single(r, metric_def, ts, sensor_data, all_timestamps):
    """Recompute a single-output derived metric (e.g. TempDelta) for all timestamps."""
    name = metric_def["name"]
    derived_key = make_key("derived", name, "raw")
    count = 0
    for timestamp_ms in sorted(all_timestamps):
        indoor_values = [
            sensor_data[s][timestamp_ms]
            for s in metric_def["indoor_sensors"]
            if timestamp_ms in sensor_data.get(s, {})
        ]
        outdoor_values = [
            sensor_data[s][timestamp_ms]
            for s in metric_def["outdoor_sensors"]
            if timestamp_ms in sensor_data.get(s, {})
        ]
        if not indoor_values or not outdoor_values:
            continue
        indoor_mean = sum(indoor_values) / len(indoor_values)
        outdoor_val = min(outdoor_values)
        delta = round(indoor_mean - outdoor_val, 2)
        try:
            ts.add(derived_key, timestamp_ms, float(delta))
            count += 1
        except redis.ResponseError as e:
            if "TSDB: Timestamp is older than retention" not in str(e):
                raise
    logger.info(f"recomputed {count} derived {name} values")


def _recompute_poly_smooth(r, metric_def, ts, sensor_ts, sensor_vals, sensor_data):
    """Recompute polynomial-smoothed temperatures across the full raw history.

    Uses a bisect-based sliding window to avoid O(N²) Redis round-trips.
    """
    import bisect
    from templogger.derived import _poly_smooth, _apparent_temp

    degree = metric_def.get("degree", 2)
    window_ms = metric_def.get("window_min", 120) * 60 * 1000

    # Fetch Humidity data for AT computation (exact timestamp lookups)
    all_sensors = metric_def["indoor_sensors"] + metric_def["outdoor_sensors"]
    sensor_humidity = {}  # {sensor: {timestamp_ms: value}}
    for sensor in all_sensors:
        hkey = make_key(sensor, "Humidity", "raw")
        try:
            hdata = ts.range(hkey, "-", "+")
            sensor_humidity[sensor] = {int(t): v for t, v in hdata}
        except redis.ResponseError:
            sensor_humidity[sensor] = {}

    # Collect all timestamps from indoor sensors
    all_timestamps = set()
    for sensor in metric_def["indoor_sensors"]:
        all_timestamps.update(sensor_ts.get(sensor, []))

    if not all_timestamps:
        logger.info(f"no raw data for poly smooth metric {metric_def['name']}")
        return

    series_redis_keys = {
        s["key"]: make_key("derived", s["key"], "raw")
        for s in metric_def["series"]
    }
    counts = {k: 0 for k in series_redis_keys}

    for timestamp_ms in sorted(all_timestamps):
        window_start = timestamp_ms - window_ms

        indoor_smoothed = []
        for sensor in metric_def["indoor_sensors"]:
            ts_list = sensor_ts.get(sensor, [])
            if not ts_list:
                continue
            lo = bisect.bisect_left(ts_list, window_start)
            hi = bisect.bisect_right(ts_list, timestamp_ms)
            t_slice = ts_list[lo:hi]
            v_slice = sensor_vals[sensor][lo:hi]
            if len(t_slice) < degree + 1:
                continue
            v = _poly_smooth(t_slice, v_slice, timestamp_ms, degree)
            if v is not None:
                indoor_smoothed.append(v)

        outdoor_smoothed = []
        for sensor in metric_def["outdoor_sensors"]:
            ts_list = sensor_ts.get(sensor, [])
            if not ts_list:
                continue
            lo = bisect.bisect_left(ts_list, window_start)
            hi = bisect.bisect_right(ts_list, timestamp_ms)
            t_slice = ts_list[lo:hi]
            v_slice = sensor_vals[sensor][lo:hi]
            if len(t_slice) < degree + 1:
                continue
            v = _poly_smooth(t_slice, v_slice, timestamp_ms, degree)
            if v is not None:
                outdoor_smoothed.append(v)

        if not indoor_smoothed or not outdoor_smoothed:
            continue

        result = {
            "PolyTempIndoor": round(sum(indoor_smoothed) / len(indoor_smoothed), 2),
            "PolyTempOutdoor": round(min(outdoor_smoothed), 2),
        }

        # Raw values at exact timestamp for comparison series
        indoor_raw = [
            sensor_data[s][timestamp_ms]
            for s in metric_def["indoor_sensors"]
            if timestamp_ms in sensor_data.get(s, {})
        ]
        outdoor_raw = [
            sensor_data[s][timestamp_ms]
            for s in metric_def["outdoor_sensors"]
            if timestamp_ms in sensor_data.get(s, {})
        ]
        if indoor_raw:
            result["RawIndoorMean"] = round(sum(indoor_raw) / len(indoor_raw), 2)
        if outdoor_raw:
            result["RawOutdoor"] = round(min(outdoor_raw), 2)

        # Apparent temperature
        indoor_rh = [
            sensor_humidity[s][timestamp_ms]
            for s in metric_def["indoor_sensors"]
            if timestamp_ms in sensor_humidity.get(s, {})
        ]
        outdoor_rh = [
            sensor_humidity[s][timestamp_ms]
            for s in metric_def["outdoor_sensors"]
            if timestamp_ms in sensor_humidity.get(s, {})
        ]
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

        for series_def in metric_def["series"]:
            key_name = series_def["key"]
            val = result.get(key_name)
            if val is None:
                continue
            try:
                ts.add(series_redis_keys[key_name], timestamp_ms, float(val))
                counts[key_name] += 1
            except redis.ResponseError as e:
                if "TSDB: Timestamp is older than retention" not in str(e):
                    raise

    for key_name, count in counts.items():
        logger.info(f"recomputed {count} derived {key_name} values")


def recompute_derived(r):
    """Recompute all derived metrics from raw data in Redis."""
    ts = r.ts()
    for metric_def in DERIVED_METRICS:
        source_metric = metric_def["source_metric"]
        all_sensors = metric_def["indoor_sensors"] + metric_def["outdoor_sensors"]

        # Fetch full time series for each involved sensor
        sensor_data = {}   # {sensor: {timestamp_ms: value}}  — for simple delta
        sensor_ts = {}     # {sensor: [timestamp_ms, ...]}     — for poly smooth
        sensor_vals = {}   # {sensor: [value, ...]}            — for poly smooth
        for sensor in all_sensors:
            key = make_key(sensor, source_metric, "raw")
            try:
                data = ts.range(key, "-", "+")
                pairs = [(int(t), v) for t, v in data]
                sensor_data[sensor] = {t: v for t, v in pairs}
                sensor_ts[sensor] = [t for t, _ in pairs]
                sensor_vals[sensor] = [v for _, v in pairs]
            except redis.ResponseError:
                sensor_data[sensor] = {}
                sensor_ts[sensor] = []
                sensor_vals[sensor] = []

        if "series" in metric_def:
            _recompute_poly_smooth(r, metric_def, ts, sensor_ts, sensor_vals, sensor_data)
        else:
            all_timestamps = set()
            for sensor in metric_def["indoor_sensors"]:
                all_timestamps.update(sensor_data.get(sensor, {}).keys())
            if not all_timestamps:
                logger.info(f"no raw data for derived metric {metric_def['name']}")
                continue
            _recompute_single(r, metric_def, ts, sensor_data, all_timestamps)


if __name__ == "__main__":
    logger.info(f"starting redis rehydration")
    r = get_redis()
    ts = r.ts()
    assert r.ping()

    r.flushdb()
    create_schema(r)

    # Step 1: ingest raw data (existing flow)
    df = ingest_raw(r, DATA_DIR_SHT, "sht")
    push_raw_sht_data_redis(r, df)

    df_co2 = ingest_raw(r, DATA_DIR_CO2, "co2")
    push_raw_co2_data_redis(r, df_co2)

    # Step 2: ingest aggregate CSVs into Redis
    for type_name, sensor_type in SENSOR_TYPES.items():
        ingest_aggregate_csvs(r, sensor_type, "hourly")
        ingest_aggregate_csvs(r, sensor_type, "daily")

    # Step 3: marker-based backfill for gaps
    for type_name, sensor_type in SENSOR_TYPES.items():
        marker_backfill(r, sensor_type)

    # Step 4: recompute derived metrics from raw data
    recompute_derived(r)

    logger.info("rehydration complete")