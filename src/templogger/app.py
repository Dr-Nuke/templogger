import datetime
import sys
import time
from typing import List, Optional, Tuple

import pandas as pd
import plotly.colors as pc
import plotly.express as px
import redis
from dash import Dash, Input, Output, dcc, html
from dash.exceptions import PreventUpdate

from templogger.config import (AGGREGATIONS, METRICS_CO2, METRICS_PLOT,
                               METRICS_SHT, REDIS_LAST_TEIMESTAMP_KEY, SENSORS_SHT,
                               SENSORS_CO2, logger)
from templogger.utils import make_key, ms_to_pandas_dt

# Redis setup
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
ts = r.ts()


def generate_n_colors(colorscale_name, n):
    # Get N colors from the specified continuous color scale
    colors = pc.sample_colorscale(colorscale_name, n)
    return colors


def ms_to_pandas_dt(series: pd.Series) -> pd.Series:
    """Convert millisecond timestamps to pandas datetime."""
    return pd.to_datetime(series, unit="ms")


def is_data_updated(reference_key: str) -> bool:
    """
    Check if new data has been added to a reference RedisTimeSeries key.
    Used to avoid unnecessary graph redraws.
    """

    try:

        try:

            last_timestamp = int(r.get(REDIS_LAST_TEIMESTAMP_KEY))
            new_timestamp = r.ts().get(reference_key)[0]
            logger.info(
                f"obtained last_timestamp: {ms_to_pandas_dt(last_timestamp)}, new timestamp: {ms_to_pandas_dt(new_timestamp)}")
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logger.info(
                f"Unexpected error ({exc_type}) in line {exc_tb.tb_lineno}: {e}")
            return False

        if (last_timestamp is None) or (last_timestamp == 0):
            r.set(REDIS_LAST_TEIMESTAMP_KEY, new_timestamp)
            logger.info(
                f"last timestamp was {last_timestamp}. setting to {new_timestamp}")

        logger.info(
            f"last_time: {last_timestamp}, new timestamp: {new_timestamp} ")
        if new_timestamp > last_timestamp:
            return True

        return False
    except Exception as e:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        logger.info(
            f"Unexpected error ({exc_type}) in line {exc_tb.tb_lineno}: {e}")
        return False


def get_night_periods(x_values: List[datetime]) -> List[Tuple[datetime, datetime]]:
    """Returns evening-to-morning periods for night shading."""
    if not x_values:
        return []

    days = {dt.replace(hour=0, minute=0, second=0, microsecond=0)
            for dt in x_values}
    periods = []
    for day in days:
        start = day + datetime.timedelta(hours=22)
        end = day + datetime.timedelta(hours=30)
        periods.append((start, end))
    return periods


def fetch_series_data(key: str) -> pd.DataFrame:
    """Fetch and return data from a RedisTimeSeries key as a DataFrame."""
    try:
        data = ts.range(key, "-", "+")
    except redis.ResponseError as e:
        if "TSDB: the key does not exist" not in str(e):
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logger.error(
                f"Unexpected error ({exc_type}) in line {exc_tb.tb_lineno}: {e}")
        return pd.DataFrame(columns=["time", "value", "datetime"])

    df = pd.DataFrame(data, columns=["time", "value"])
    df["datetime"] = ms_to_pandas_dt(df["time"])
    return df


def plot_lines_for(metric: str, agg: str) -> List[dict]:
    """Build all sensor lines for a given metric and aggregation."""
    lines = []
    if metric != "CO2":
        for sensor in [s.name for s in SENSORS_SHT]:
            key = make_key(sensor, metric, agg)
            label = f"{sensor}"
            df = fetch_series_data(key)
            if not df.empty:
                lines.append({
                    "x": df["datetime"],
                    "y": df["value"],
                    "type": "line",
                    "name": label
                })
    else: 
        for sensor in [s["location"] for s in SENSORS_CO2]:
            key = make_key(sensor, metric, agg)
            label = f"{sensor}"
            df = fetch_series_data(key)
            if not df.empty:
                lines.append({
                    "x": df["datetime"],
                    "y": df["value"],
                    "type": "line",
                    "name": label
                })

    
    return lines


def register_callbacks(app: Dash) -> None:
    output_ids = [
        Output(f"graph-{metric}-{agg}", "figure")
        for metric in METRICS_PLOT
        for agg, _ in AGGREGATIONS.items()

    ]

    @app.callback(output_ids, Input('interval-component', 'n_intervals'))
    def update_graphs(n: int):
        start = time.time()
        reference_key = make_key(SENSORS_SHT[0].name, METRICS_SHT[0], "raw")

        # Always fetch on first load, skip if no new data later
        if n != 0 and not is_data_updated(reference_key):
            logger.info(f"no Graph update {n} due to no new data")
            raise PreventUpdate

        figures = []
        for metric in METRICS_PLOT:
            for agg, _ in AGGREGATIONS.items():
                lines = plot_lines_for(metric, agg)
                figures.append({
                    "data": lines,
                    "layout": {
                        "title": f"{metric} ({agg})",
                        "xaxis": {"title": "Time"},
                        "yaxis": {"title": metric},
                        "margin": {"l": 30, "r": 10, "t": 30, "b": 30},
                        "height": 250
                    }
                })

        logger.info(f"Graph update {n} took {time.time() - start:.2f}s")
        return figures


def serve_layout() -> html.Div:
    rows = []
    graph_width = "30%"

    # Real data rows: AGGREGATIONS × METRICS
    for metric in METRICS_PLOT:
        row = html.Div(
            children=[
                dcc.Graph(
                    id=f"graph-{metric}-{agg}", style={"display": "inline-block", "width": graph_width})
                for agg, _ in AGGREGATIONS.items()
            ],
            style={"display": "flex", "justifyContent": "space-between",
                   "marginBottom": "20px"}
        )
        rows.append(row)

    # Placeholder row
    placeholder_row = html.Div(
        children=[
            dcc.Graph(
                id=f"graph-placeholder-{i}", style={"display": "inline-block", "width": graph_width})
            for i in range(len(AGGREGATIONS))
        ],
        style={"display": "flex", "justifyContent": "space-between",
               "marginBottom": "20px"}
    )
    rows.append(placeholder_row)

    return html.Div([
        *rows,
        dcc.Interval(id='interval-component',
                     interval=10 * 1000, n_intervals=0)
    ])


if __name__ == "__main__":
    # Initialize and run app
    app = Dash(__name__)
    app.title = "Sensor Dashboard"
    app.layout = serve_layout
    register_callbacks(app)
    app.run(host="0.0.0.0", port=8050, debug=True)
