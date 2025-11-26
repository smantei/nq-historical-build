import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# -----------------------------
# CONFIG
# -----------------------------
OUTPUT_DIR = Path("output")
OHLC_5M_PATH = Path("data/00_nq_ohlc_5m.csv")

# -----------------------------
# CLOUD DETECTION & PASSWORD GATE
# -----------------------------
CLOUD_MARKER_FILE = Path(".cloud")   # create this file in the cloud repo to enable password
IS_CLOUD = CLOUD_MARKER_FILE.exists()

# Password: can be set via env var in cloud
APP_PASSWORD = "".join([chr(x) for x in [109, 111, 115, 101]])


def check_password():
    if not IS_CLOUD:
        return

    if "password_ok" not in st.session_state:
        st.session_state.password_ok = False

    # If not logged in, show ONLY the login screen
    if not st.session_state.password_ok:
        st.title("NQ Event Viewer (Protected)")
        pw = st.text_input("Enter password to access the app:", type="password")

        if pw:
            if pw == APP_PASSWORD:
                st.session_state.password_ok = True
                st.rerun()
            else:
                st.error("Incorrect password")

        st.stop()


# -----------------------------
# HELPERS
# -----------------------------
@st.cache_data
def list_event_files(output_dir: Path):
    if not output_dir.exists():
        return []
    files = sorted(output_dir.glob("event*.json"))
    return files


@st.cache_data
def load_event(path: Path):
    with open(path, "r") as f:
        data = json.load(f)
    return data


@st.cache_data
def load_ohlc_5m(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Parse timestamps
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True, errors="coerce")
    return df


def parse_ts(value: str):
    """
    Safely parse a timestamp string (handles 'T' vs ' ').
    All returned as UTC-aware.
    """
    if value is None:
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    return ts


def get_time_window(event: dict):
    """
    Decide what time range to show on the 5m chart.
    Base: BOS window (if available), else window_start/window_end, else touch +/- 1h.
    """
    window = event.get("window", {})
    touch = event.get("touch", {})
    fvg_5m = event.get("fvg_5m_in_window", [])
    bos_5m = event.get("bos_5m_in_window", [])

    candidates_start = []
    candidates_end = []

    # BOS window from event["window"]
    bos_window_start = parse_ts(window.get("bos_window_start"))
    bos_window_end = parse_ts(window.get("bos_window_end"))
    if bos_window_start is not None:
        candidates_start.append(bos_window_start)
    if bos_window_end is not None:
        candidates_end.append(bos_window_end)

    # Touch window
    window_start = parse_ts(touch.get("window_start"))
    window_end = parse_ts(touch.get("window_end"))
    if window_start is not None:
        candidates_start.append(window_start)
    if window_end is not None:
        candidates_end.append(window_end)

    # Touch actual event time
    touch_ts = parse_ts(touch.get("ts_event"))
    if touch_ts is not None:
        candidates_start.append(touch_ts)
        candidates_end.append(touch_ts)

    # 5m FVGs
    for f in fvg_5m:
        stime = parse_ts(f.get("start_time"))
        if stime is not None:
            candidates_start.append(stime)
            candidates_end.append(stime)

    # BOS 5m
    for b in bos_5m:
        ts = parse_ts(b.get("ts_event"))
        if ts is not None:
            candidates_start.append(ts)
            candidates_end.append(ts)

    if not candidates_start or not candidates_end:
        return None, None

    t_min = min(candidates_start)
    t_max = max(candidates_end)

    # Add padding (e.g., 1 hour on each side)
    padding = pd.Timedelta(hours=1)
    return t_min - padding, t_max + padding


def build_chart(df_5m: pd.DataFrame, event: dict) -> go.Figure:
    """
    Build Plotly candlestick chart with event markers.
    """
    touch = event.get("touch", {})
    fvg_5m = event.get("fvg_5m_in_window", [])
    bos_5m = event.get("bos_5m_in_window", [])
    hourly = event.get("hourly_fvg", {})
    window = event.get("window", {})
    trade_signals = event.get("trade_signals", [])  # contains buy_long / buy_short etc.

    # Determine base time range
    start_ts, end_ts = get_time_window(event)

    # EXTEND end_ts to stop-loss / take-profit candle + 1 hour
    for sig in trade_signals:
        exit_ts = parse_ts(sig.get("exit_ts"))
        if exit_ts is not None:
            extended_end = exit_ts + pd.Timedelta(hours=1)
            if end_ts is None or extended_end > end_ts:
                end_ts = extended_end

    # Apply time window
    if start_ts is not None and end_ts is not None:
        df = df_5m[(df_5m["ts_event"] >= start_ts) & (df_5m["ts_event"] <= end_ts)].copy()
    else:
        df = df_5m.copy()

    fig = go.Figure()

    # Candlestick trace
    fig.add_trace(
        go.Candlestick(
            x=df["ts_event"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="",  # removing
        )
    )

    # -----------------
    # Touch marker
    # -----------------
    touch_ts = parse_ts(touch.get("ts_event"))
    touch_price = touch.get("price_touched")
    if touch_ts is not None and touch_price is not None:
        fig.add_trace(
            go.Scatter(
                x=[touch_ts],
                y=[touch_price],
                mode="markers+text",
                name="Touch",
                text=["Touch"],
                textposition="top center",
                marker=dict(size=10, symbol="x"),
            )
        )

    # -----------------
    # BOS markers
    # -----------------
    bos_x = []
    bos_y = []
    bos_text = []
    for b in bos_5m:
        ts = parse_ts(b.get("ts_event"))
        if ts is None:
            continue
        bos_x.append(ts)
        bos_y.append(b.get("trigger_close"))
        dir_label = b.get("bos_direction", "")
        bos_text.append(dir_label)

    if bos_x:
        fig.add_trace(
            go.Scatter(
                x=bos_x,
                y=bos_y,
                mode="markers+text",
                name="BOS",
                text=bos_text,
                textposition="top center",
                marker=dict(size=9, symbol="triangle-up"),
            )
        )

    # -----------------
    # 5m FVG markers (midpoint price) + rectangles
    # -----------------
    fvg_x = []
    fvg_y = []
    fvg_text = []
    fvg_meta_by_id = {}  # map fvg_5m_id -> {"start_ts": ts, "mid_price": mid}

    # x1 for the rectangles = right edge of the visible data window
    x1_rect = df["ts_event"].max() if not df.empty else None

    for f in fvg_5m:
        stime = parse_ts(f.get("start_time"))
        if stime is None:
            continue

        begin = f.get("begin_bound")
        end = f.get("end_bound")
        if begin is None or end is None:
            continue

        # midpoint for the marker
        mid = (begin + end) / 2.0
        fvg_x.append(stime)
        fvg_y.append(mid)

        fvg_id = f.get("fvg_5m_id", "")
        if fvg_id:
            fvg_meta_by_id[fvg_id] = {
                "start_ts": stime,
                "mid_price": mid,
            }

        # include FT / CT indices in the label
        ft = f.get("index_first_touch")
        ct = f.get("index_valid_close_after_touch")

        if ft is not None and ct is not None:
            label = f"{fvg_id} (FT{int(ft)}/CT{int(ct)})"
        elif ft is not None:
            label = f"{fvg_id} (FT{int(ft)})"
        elif ct is not None:
            label = f"{fvg_id} (CT{int(ct)})"
        else:
            label = fvg_id

        fvg_text.append(label)

        # light blue rectangle (same idea as hourly FVG but starting at stime)
        if x1_rect is not None:
            fig.add_shape(
                type="rect",
                x0=stime,
                x1=x1_rect,  # extend into the "future" / right side of chart
                y0=min(begin, end),
                y1=max(begin, end),
                fillcolor="rgba(173, 216, 230, 0.25)",  # faded light blue
                line=dict(width=0),
                layer="below",
            )

    if fvg_x:
        fig.add_trace(
            go.Scatter(
                x=fvg_x,
                y=fvg_y,
                mode="markers+text",
                name="5m FVG",
                text=fvg_text,
                textposition="bottom center",
                marker=dict(size=8, symbol="square", color="blue"),
            )
        )

    # -----------------
    # BUY SIGNAL MARKERS (from trade_signals)
    # -----------------
    buy_x = []
    buy_y = []
    buy_text = []

    for sig in trade_signals:
        sig_type = sig.get("signal", "")
        fvg_id = sig.get("fvg_5m_id")
        idx_ct = sig.get("index_valid_close_after_touch", 0)

        if not fvg_id or not isinstance(idx_ct, int) or idx_ct <= 0:
            continue

        if sig_type not in ("buy_long", "buy_short"):
            continue

        meta = fvg_meta_by_id.get(fvg_id)
        if meta is None:
            continue

        start_ts_fvg = meta["start_ts"]

        # Rebuild indexing logic: slice from C3 onward
        df_slice = (
            df_5m[df_5m["ts_event"] >= start_ts_fvg]
            .sort_values("ts_event")
            .reset_index(drop=True)
        )

        if len(df_slice) <= idx_ct:
            continue

        trade_row = df_slice.iloc[idx_ct]
        trade_ts = trade_row["ts_event"]
        trade_price = float(trade_row["close"])

        # Abbreviated label
        if sig_type == "buy_long":
            label = "buy_l"
        else:
            label = "buy_s"

        buy_x.append(trade_ts)
        buy_y.append(trade_price)
        buy_text.append(label)

    if buy_x:
        fig.add_trace(
            go.Scatter(
                x=buy_x,
                y=buy_y,
                mode="markers+text",
                name="Buys",
                text=buy_text,
                textposition="top center",
                textfont=dict(color="#FF00FF"),   # magenta 
                marker=dict(
                    size=12,
                    symbol="triangle-up",
                    color="#FF00FF",           
                ),
            )
        )

    # -----------------
    # SELL SIGNAL MARKERS
    # -----------------
    sell_x = []
    sell_y = []
    sell_text = []

    for sig in trade_signals:
        exit_sig = sig.get("exit_signal")
        exit_ts = parse_ts(sig.get("exit_ts"))
        exit_price = sig.get("exit_price")

        if exit_sig and exit_ts and exit_price is not None:
            sell_x.append(exit_ts)
            sell_y.append(float(exit_price))
            sell_text.append(exit_sig)

    if sell_x:
        fig.add_trace(
            go.Scatter(
                x=sell_x,
                y=sell_y,
                mode="markers+text",
                name="Sells",
                text=sell_text,
                textposition="top center",
                textfont=dict(color="#FF00FF"),  # magenta
                marker=dict(
                    size=12,
                    symbol="triangle-down",
                    color="#FF00FF"  # magenta
                ),
            )
        )

    # -----------------
    # Hourly FVG band (as horizontal region)
    # -----------------
    begin_bound = hourly.get("begin_bound")
    end_bound = hourly.get("end_bound")

    if begin_bound is not None and end_bound is not None and not df.empty:
        x0 = df["ts_event"].min()
        x1 = df["ts_event"].max()
        fig.add_shape(
            type="rect",
            x0=x0,
            x1=x1,
            y0=min(begin_bound, end_bound),
            y1=max(begin_bound, end_bound),
            fillcolor="rgba(200, 200, 200, 0.2)",
            line=dict(width=0),
            layer="below",
        )

    # -----------------
    # BOS window vertical lines
    # -----------------
    bos_window_start = parse_ts(window.get("bos_window_start"))
    bos_window_end = parse_ts(window.get("bos_window_end"))

    for ts in [bos_window_start, bos_window_end]:
        if ts is not None:
            fig.add_vline(
                x=ts,
                line=dict(width=1, dash="dot"),
            )

    fig.update_layout(
        title=f"Event {event.get('event_id', '')} – 5m Chart",
        xaxis_title="Time (UTC)",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        height=900,
        yaxis=dict(
            tickformat=",.2f",
            exponentformat="none",
            showexponent="none",
        ),
    )

    return fig


# -----------------------------
# STREAMLIT APP
# -----------------------------
def main():
    st.set_page_config(page_title="NQ Event Viewer", layout="wide")

    # ---- Password gate (only active if .cloud exists) ----
    check_password()

    st.title("NQ Event Viewer")

    # Sidebar: event selection
    st.sidebar.header("Event selection")

    event_files = list_event_files(OUTPUT_DIR)
    if not event_files:
        st.sidebar.error(f"No event JSON files found in {OUTPUT_DIR}/")
        st.stop()

    # Show pretty labels like EV00001 (filename) if possible
    labels = []
    for f in event_files:
        try:
            data = load_event(f)
            ev_id = data.get("event_id", f.stem)
        except Exception:
            ev_id = f.stem
        labels.append(f"{ev_id} ({f.name})")

    selected_label = st.sidebar.selectbox("Choose event", options=labels)
    idx = labels.index(selected_label)
    selected_file = event_files[idx]

    event = load_event(selected_file)

    # ---- Sidebar summary ----
    st.sidebar.markdown("---")
    st.sidebar.subheader("Summary")

    summary = event.get("summary", {})
    hourly = event.get("hourly_fvg", {})

    hourly_id = hourly.get("fvg_hour_id", "N/A")
    hourly_start = hourly.get("start_time", "N/A")
    hourly_dir = hourly.get("direction", "N/A")
    hourly_end_bound = hourly.get("end_bound", "N/A")

    st.sidebar.write(f"**Event ID:** {event.get('event_id', 'N/A')}")
    st.sidebar.write(f"**Hourly FVG ID:** {hourly_id}")
    st.sidebar.write(f"**Hourly FVG start:** {hourly_start}")

    # Big colored arrow for direction
    if hourly_dir == "up":
        arrow_html = """
        <div style="display:flex;align-items:center;gap:8px;">
          <span><strong>Hourly FVG direction:</strong> up</span>
          <span style="color:#00cc44; font-size:26px; line-height:1;">▲</span>
        </div>
        """
    elif hourly_dir == "down":
        arrow_html = """
        <div style="display:flex;align-items:center;gap:8px;">
          <span><strong>Hourly FVG direction:</strong> down</span>
          <span style="color:#ff3333; font-size:26px; line-height:1;">▼</span>
        </div>
        """
    else:
        arrow_html = f"**Hourly FVG direction:** {hourly_dir}"

    st.sidebar.markdown(arrow_html, unsafe_allow_html=True)

    st.sidebar.write(f"**Hourly FVG end_bound:** {hourly_end_bound}")

    # -----------------
    # Trade details
    # -----------------
    trade_signals = event.get("trade_signals", [])

    if not trade_signals:
        st.sidebar.write("**Trades:** None")
    else:
        st.sidebar.markdown("### Trade")

        for sig in trade_signals:
            st.sidebar.write(f"**Type:** {sig.get('signal', 'N/A')}")
            st.sidebar.write(f"**Entry Time:** {sig.get('entry_ts', 'N/A')}")
            st.sidebar.write(f"**Entry Price:** {sig.get('entry_price', 'N/A')}")
            st.sidebar.write(f"**Stop Loss:** {sig.get('stop_loss', 'N/A')}")
            st.sidebar.write(f"**Take Profit:** {sig.get('take_profit', 'N/A')}")
            st.sidebar.write(f"**Exit Type:** {sig.get('exit_signal', 'N/A')}")
            st.sidebar.write(f"**Exit Time:** {sig.get('exit_ts', 'N/A')}")
            st.sidebar.write(f"**Exit Price:** {sig.get('exit_price', 'N/A')}")
            st.sidebar.markdown("---")

    # ---- Main: chart only ----
    st.subheader("Chart")

    if not OHLC_5M_PATH.exists():
        st.error(f"OHLC file not found: {OHLC_5M_PATH}")
        return

    df_5m = load_ohlc_5m(OHLC_5M_PATH)
    fig = build_chart(df_5m, event)
    st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
