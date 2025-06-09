from dash import Dash, html, dcc, Input, Output
from dash.exceptions import PreventUpdate
import redis
import pandas as pd
from typing import List, Tuple, Optional
import sys
from config import logger, METRICS, SENSORS, AGGREGATIONS


# Redis setup
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
ts = r.ts()

# Timestamp cache to detect updates
_last_timestamp: Optional[int] = None


def make_key(loc: str, metric: str, agg: str) -> str:
    """Creates a Redis key for a given location, metric, and aggregation."""
    return f"sensor:{loc}:{metric}:{agg}"


def ms_to_pandas_dt(series: pd.Series) -> pd.Series:
    """Convert millisecond timestamps to pandas datetime."""
    return pd.to_datetime(series, unit="ms")


def is_data_updated(reference_key: str) -> bool:
    """
    Check if new data has been added to a reference RedisTimeSeries key.
    Used to avoid unnecessary graph redraws.
    """
    global _last_timestamp
    try:
        result = ts.get(reference_key)
    except redis.ResponseError as e:
        if "TSDB: the key does not exist" not in str(e):
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logger.error(
                f"Unexpected error ({exc_type}) in line {exc_tb.tb_lineno}: {e}")
        return False

    if result is None:
        return False

    last_time, _ = result
    if (_last_timestamp is None) or (last_time > _last_timestamp):
        _last_timestamp = last_time
        return True
    return False


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
    for sensor in [s.name for s in SENSORS]:
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
        for agg,_ in AGGREGATIONS.items()
        for metric in METRICS
    ]

    @app.callback(output_ids, Input('interval-component', 'n_intervals'))
    def update_graphs(n: int):
        logger.info(f"Interval {n} triggered")
        reference_key = make_key(SENSORS[0], METRICS[0], "raw")

        # Always fetch on first load, skip if no new data later
        if n != 0 and not is_data_updated(reference_key):
            raise PreventUpdate

        figures = []
        for agg,_ in AGGREGATIONS.items():
            for metric in METRICS:
                lines = plot_lines_for(metric, agg)
                figures.append({
                    "data": lines,
                    "layout": {
                        "title": f"{metric} LOL ({agg})",
                        "xaxis": {"title": "Time"},
                        "yaxis": {"title": metric},
                        "margin": {"l": 30, "r": 10, "t": 30, "b": 30},
                        "height": 250
                    }
                })
        return figures


def serve_layout() -> html.Div:
    rows = []

    # Real data rows: AGGREGATIONS × METRICS
    for agg,_ in AGGREGATIONS.items():
        row = html.Div(
            children=[
                dcc.Graph(id=f"graph-{metric}-{agg}", style={"display": "inline-block", "width": "19%"})
                for metric in METRICS
            ],
            style={"display": "flex", "justifyContent": "space-between", "marginBottom": "20px"}
        )
        rows.append(row)

    # Placeholder row
    placeholder_row = html.Div(
        children=[
            dcc.Graph(id=f"graph-placeholder-{i}", style={"display": "inline-block", "width": "19%"})
            for i in range(len(METRICS))
        ],
        style={"display": "flex", "justifyContent": "space-between", "marginBottom": "20px"}
    )
    rows.append(placeholder_row)

    return html.Div([
        html.H2("Live Sensor Dashboard"),
        *rows,
        dcc.Interval(id='interval-component', interval=10 * 1000, n_intervals=0)
    ])


# Initialize and run app
app = Dash(__name__)
app.title = "Sensor Dashboard"
app.layout = serve_layout
register_callbacks(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
