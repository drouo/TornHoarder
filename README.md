# TornHoarder

**TornHoarder** turns a hoard of Torn items into cash without giving anything away. It pulls your inventory through the Torn API, prices every item against the live Item Market, and decides *how* each one should go: dump it, undercut, hold out for top dollar, sell it to a city shop, or keep it. Then it does the tedious part in your browser. A Tampermonkey script fills Add Listing prices and walks you through city-shop sales with one button. Every sale is still your click.

Originally created by **Victor Belinchón** ([Kzon [3968250]](https://www.torn.com/profiles.php?XID=3968250)) as *Torn Market Analyzer*. Now maintained by [TheOldFarmer [3653623]](https://www.torn.com/profiles.php?XID=3653623).

## Quick start

1. Install Python 3.10+, then run `pip install -r requirements.txt`.
2. Start the app with `streamlit run app/streamlit_app.py`. On Windows you can use `powershell -ExecutionPolicy Bypass -File tools\server.ps1 start` instead.
3. Open http://localhost:8501, enter a **Limited** Torn API key, choose your **Sell categories**, and click **Run**.
4. On the **Autofill script** tab, click the link to install the script into [Tampermonkey](https://www.tampermonkey.net/). Use one Tampermonkey only, not the regular and BETA builds together.
5. On the **Sell plan** tab, click **Start shop run →** for city-shop sales, then open Torn's Add Listing page for everything else.

The rest of this README describes how the pricing works.

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
  - **$1 under the cheapest clean listing**: buyers take the cheapest listing first

### 5) Sell plan

Only items in your **Sell categories** are pulled and priced; everything else is never listed. The default is unequipped weapons (Primary, Secondary, Melee) and armor (Defensive), plus general loot (Clothing, Collectible, Jewelry, Other, Tool, Material, Car) and museum items (Plushie, Flower, Artifact). Drugs, medical, boosters, energy drinks, supply packs, books and temporary weapons are kept unless you add them.

Each item in a sell category gets a plan automatically. The **Sell plan** tab lets you change it, and changes are saved in `~/.tma_config.json`:

| Plan | When (defaults) | List price |
|---|---|---|
| **Dump** | worth < $20k each and the stack < $1M | 5% under the cheapest listing |
| **Competitive** | worth ≥ $20k each, or a stack ≥ $1M | cheapest listing − $1 |
| **Top dollar** | weapon/armor copy with quality ≥ 50, or ≥ $20k with < 20 listings | Greedy (upper quartile) |
| **Shop** | a city shop pays ≥ the market price after Torn's 5% sales tax | the shop's price, no listing |
| **Keep** | only when you choose it | never listed |

The thresholds can be changed under **Rules**. The autofill script fills each row from its plan and leaves Shop and Keep rows blank. The item catalog (reference and shop prices) re-downloads from `torn/items` once it's more than 24h old.



### Keeping what you need

Only the surplus is ever sold. The **Keeping** column on the Sell plan tab shows how many of each item stay in your inventory; type a number there to set it by hand. The defaults:

- **Crime items are all kept** by default: everything an Organized Crime requires (from Torn's `torn/organizedcrimes` list) and everything whose in-game effect says a crime uses it (Forgery, Disposal, Graffiti, Arson…). Untick **Keep all crime items** under Rules to keep only a stock and sell the rest: 1 of each reusable OC tool, 3 of each item an OC uses up, and 1 of other crime items.
- **Clothing and Collectibles**: keep 1 of each, so only duplicates are sold.
- **Weapons and armor**: the copies kept are your best by quality.

Before anything sells, the shop run lists each item × quantity × price above its button (click a line to leave it out), and Torn asks you to confirm. On Add Listing, click the status box to see every row it filled and every row it held, and why.

### 6) Shop run

Shop-plan items are sold to city shops with one button. **Start shop run →** on the Sell plan tab opens the first shop. On every shop page the userscript puts one green button in the same spot:

1. **Sell N item(s) here · $X**: fills only this run's Shop items (weapon/armor copies matched by their armoryID) and opens Torn's "Are you sure?" with the total.
2. **Confirm: sell …**: confirms that one sale.
3. **Next: <shop> →**: goes to the next shop, until **Shop run done ✓**.

Each shop page also reports what it really lists back to the app's local server (`POST 127.0.0.1:9999/shop-report`, stored in `data/shop_buys.json`). The catalog's vendor is only a guess, and Big Al's, for example, buys a few of the weapons it stocks. Later runs route items only to shops seen buying them. Shop-plan items still in your inventory on the Add Listing page are listed with their market plan instead.

## Project structure

```

TornHoarder/
├─ app/
│  ├─ streamlit_app.py      # input form + pipeline orchestration
│  ├─ paths.py               # shared path constants
│  └─ ui/                    # one results tab per module
│     ├─ sell_plan.py        # per-item Dump/Competitive/Top/Shop/Keep plans
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
│     ├─ sell_plan.py        # sell-plan classifier (pure logic)
│     ├─ catalog.py          # torn/items dictionary + profiles, auto-refresh
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



## Torn API key & privacy

| | |
|---|---|
| **Key access level** | **Limited**: inventory pulls need `user → inventory`. Market and item lookups work with any key. |
| **What it calls** | `user/inventory`, `torn/{uid}?selections=itemdetails`, `market/{id}/itemmarket`, `torn/items`. All read-only. |
| **Key storage** | Only on your PC, in `~/.tma_config.json`, and only if **Remember API key** is ticked. |
| **Data sharing** | None. Requests go straight from your PC to `api.torn.com`. The userscript talks only to Torn and to this app on `127.0.0.1` (your own machine). |
| **Purpose** | Pricing and selling your own items. |

TornHoarder never buys, sells or lists anything by itself. The userscript fills in fields and shows one button, and each sale or listing happens only when you click it: one click, one action, in line with Torn's scripting rules.
