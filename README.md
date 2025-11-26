# NQ Event Viewer

A lightweight **Streamlit app** for visualizing NQ (Nasdaq futures) trading events on a 5-minute candlestick chart.  
The app loads event JSON files and overlays market structure, FVGs, BOS levels, and trade entries/exits on top of historical OHLC data.

---

## Features

- Interactive **Plotly candlestick charts**
- Event-driven overlays:
  - Fair Value Gaps (FVGs)
  - Break of Structure (BOS)
  - Touch points
  - Trade entries and exits
- Sidebar event browser
- Works locally and in the cloud
- Optional password gate when deployed publicly

---

## Project Structure

```bash
.
├── app.py                     # Main Streamlit application
├── requirements.txt           # Python dependencies
├── output/                    # Generated event JSON files
│   ├── event_0001.json
│   ├── event_0002.json
│   └── ...
├── data/
│   └── 00_nq_ohlc_5m.csv      # 5-minute OHLC source data
├── .cloud                     # (Optional) enables password gate in the cloud
└── README.md



streamlit run app.py