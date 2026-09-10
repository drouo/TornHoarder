# Torn Market Analyzer

**Torn Market Analyzer** is a Python + Streamlit tool to help you price **Item Market** listings in the online game **Torn**.

You paste the raw text from Torn’s **Item Market -> Add Listing** page, the app matches items to Torn item IDs using a local dictionary, fetches up to the first **100 sell listings** per item from Torn’s **v2 API**, cleans the order book, and outputs **three market-aware price suggestions** per item.



## Output

For each matched item, the app suggests:

- **Fast-sell price** (optimized for quick execution)
- **Fair price** (robust central estimate)
- **Greedy price** (upper-quartile strategy)

The UI also shows parsed items, unmatched items, and market diagnostics.



## How it works

### 1) Parsing & matching

- Paste the text copied from Torn’s **Add Listing** page.
- The parser extracts **item names** and **quantities**.
- Lines tagged as **Equipped** or **Untradable** are ignored.
- Item names are resolved through a local dictionary:
  - `data/torn_item_dictionary.csv` (columns: `key`, `id`)

> Matching is **dictionary-based**. Unmatched items are displayed in the UI.

### 2) Market fetch (read-only)

- Calls Torn v2 endpoint: `GET /market/{item_id}/itemmarket`
- Loads up to **100 sell listings** per item (`price`, `amount`).
- Uses a simple **token-bucket rate limiter** to stay under Torn’s request limits.

### 3) Market cleaning & market-type detection

Per item, the order book is processed as follows:

- Sort by price and compute **cumulative quantity** (market depth).
- Estimate robust center/spread using **median** and **MAD** (median absolute deviation).
- Flag extreme prices using a **robust z-score**.
- Detect **thin / exclusive** markets when either:
  - total clean units ≤ **200**, or
  - a single price level represents ≥ **50%** of the clean volume.
- Remove suspected **anchor listings** for pricing (unless removal would leave no data).

### 4) Price suggestions

All prices are computed from the **cleaned** order book:

- **Fair price**
  - Weighted median (normal markets) or unweighted median (thin/exclusive markets)
- **Greedy price**
  - Upper quartile (Q3), weighted or unweighted depending on market type
- **Fast-sell price**
  - Thin/exclusive markets: around the **3rd cheapest** clean listing
  - Unit-style markets: around the **10th cheapest** clean listing
  - Bulk markets: first price where cumulative clean depth reaches **100 units** (or full depth if < 100)
  - **Undercut by $1 only in bulk markets**



## Project structure

```

torn-market-analyzer/
├─ app/
│  ├─ streamlit_app.py      # input form + pipeline orchestration
│  ├─ paths.py               # shared path constants
│  └─ ui/                    # one results tab per module
│     ├─ pricing_table.py    # price suggestions + listing-text builder
│     ├─ special_finds.py    # copies priced above the item's standard curve
│     ├─ autofill_export.py  # Tampermonkey userscript generation/download
│     └─ diagnostics.py      # parsed/unmatched/market/long/summary tables
├─ src/
│  └─ tma/
│     ├─ http_client.py      # rate limiting, retries, Torn API error handling
│     ├─ inventory.py        # user/inventory fetch + pagination
│     ├─ inventory_matcher.py
│     ├─ market_fetch.py     # item-market listings fetch (concurrent)
│     ├─ market_enrichment.py
│     ├─ item_stats.py       # stat-value model / quality-curve fitting
│     ├─ quality.py
│     ├─ userscript.py
│     ├─ config.py           # local API-key storage
│     └─ __init__.py
├─ data/
│  ├─ torn_item_dictionary.csv
│  └─ torn_item_profiles.json
├─ tools/
│  ├─ dictionary_v2.py
│  ├─ calibrate_model.py
│  ├─ update_server.py       # serves the autofill script for Tampermonkey auto-update
│  └─ output/
│     ├─ torn_items_v2.json
│     └─ torn_item_dictionary.csv
├─ tests/
├─ requirements.txt
└─ README.md

````



## Requirements

- Python **3.10+**
- Install dependencies from `requirements.txt`



## Run locally

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
````

Open the local URL shown in the terminal (typically `http://localhost:8501`).



## Regenerate the item dictionary

If Torn adds/renames items, regenerate the dictionary from the API:

```bash
python tools/dictionary_v2.py
```

The script saves:

* `tools/output/torn_items_v2.json`
* `tools/output/torn_item_profiles.json`
* `tools/output/torn_item_dictionary.csv`

Copy the generated CSV and profiles JSON into `data/` (`data/torn_item_dictionary.csv`, `data/torn_item_profiles.json`) for the app to use the refreshed data. The app loads the item dictionary and the stat/price profile model from `data/`.



## Torn API usage & privacy

* Uses a **public (read-only)** Torn API key.
* Performs **read-only** requests to the Item Market endpoint.
* No keys or market data are transmitted anywhere except directly to Torn.
* The key is stored only in Streamlit session state while the app is running.


