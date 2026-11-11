# TornHoarder

[![Latest release](https://img.shields.io/github/v/release/drouo/TornHoarder)](https://github.com/drouo/TornHoarder/releases)

**TornHoarder** turns a hoard of Torn items into cash without giving anything away. It pulls your inventory through the Torn API, prices every item against the live Item Market, and decides *how* each one should go: dump it, undercut, hold out for top dollar, sell it to a city shop, or keep it. Then a Tampermonkey script does the tedious part in your browser, with one button you click in the same spot over and over: once on each city shop page, then on the Item Market's Add Listing page. Every sale and listing is still your click.

Originally created by **Victor Belinchón** ([Kzon [3968250]](https://www.torn.com/profiles.php?XID=3968250)) as *Torn Market Analyzer*. Now maintained by [TheOldFarmer [3653623]](https://www.torn.com/profiles.php?XID=3653623).

## Quick start

1. Install Python 3.10+, then run `pip install -r requirements.txt`.
2. Start the app with `streamlit run app/streamlit_app.py`. On Windows you can use `powershell -ExecutionPolicy Bypass -File tools\server.ps1 start` instead.
3. Open http://localhost:8501, enter a **Limited** Torn API key, choose your **Sell categories**, and click **Run**. The first run reads every weapon/armor copy's stats and takes a while. Those stats never change, so later runs reuse them (`data/uid_stats.json`) and only fetch fresh market prices.
4. On the **Autofill script** tab, click the link to install the script into [Tampermonkey](https://www.tampermonkey.net/). Use one Tampermonkey only, not the regular and BETA builds together. The app serves the script at `http://127.0.0.1:9999/tornhoarder.user.js`. After each **Run**, Tampermonkey's *Check for userscript updates* picks up the new prices.
5. Check the **Sell plan** tab, and change any plan or **Keeping** number you disagree with.
6. Click **Start shop run →** and sell the Shop items (see [Shop run](#shop-run)).
7. Click **Open Add Listing →** and list everything else (see [Listing on the Item Market](#listing-on-the-item-market)).

After a shop run, click **Run** again before listing so the plan no longer counts what you just sold.

The app header shows the version you're running; the userscript carries the same version number.

## Input sources

| Source | What it does |
|---|---|
| **API inventory** (default) | Pulls your inventory with `user/inventory`, limited to your **Sell categories**, and reads each weapon/armor copy's rolls. Equipped items are skipped. |
| **Paste items** | Paste the text from Torn's Add Listing page. Item names and quantities are matched against `data/torn_item_dictionary.csv`; lines tagged **Equipped** or **Untradable** are ignored, and unmatched lines are shown in the UI. |
| **Single item** | Price one item by name or ID, with how many you own. Add a copy's `uid` to price that exact weapon/armor copy from its real stats. |

The sell plan, shop run and Add Listing automation are built for **API inventory**, since they need your real quantities and copy stats.

## How pricing works

### Market fetch (read-only)

- Calls the Torn v2 endpoint `GET /market/{item_id}/itemmarket` and loads up to **100 sell listings** per item (`price`, `amount`).
- A token-bucket rate limiter keeps requests under Torn's limits.
- The item catalog (`torn/items`: reference market price, shop sell price, vendor, description) is re-downloaded once it's more than 24h old.

### Cleaning the order book

- Sort by price and compute cumulative quantity (market depth).
- Estimate the center and spread with the **median** and **MAD** (median absolute deviation), and flag extreme prices by robust z-score.
- Detect **thin/exclusive** markets: total clean units ≤ 200, or one price level holding ≥ 50% of the clean volume.
- Drop suspected **anchor listings**, such as bait listings far under the median, unless that would leave no data.

### Price suggestions

| Price | How |
|---|---|
| **Fast-sell** | $1 under the cheapest clean listing. Buyers take the cheapest listing first. |
| **Fair** | Weighted median (normal markets) or unweighted median (thin markets). |
| **Greedy** | Upper quartile (Q3), weighted or unweighted the same way. |

### Weapons and armor

Copies of the same weapon differ by their rolls (damage and accuracy for weapons, armor for armor). TornHoarder fits a stat-value model per item from `data/torn_item_profiles.json`, turns each copy's rolls into a **quality** score, and prices it from a quality curve built from the market's own listings. The **Pricing table** tab shows these copy-level prices, and **Special finds** lists copies worth well above the item's standard price.

### Price guards

Nothing is ever listed for a silly price:

- Fast-sell ignores listings under half the clean median, so one $1 troll listing can't set the floor.
- A listing is never priced below what a city shop would pay after the 5% tax (`ceil(shop price / 0.95)`). Below that, the item goes to the shop instead.
- A listing more than 50% under Torn's reference market price is held back, not listed.
- No weapon/armor copy lists below the item's fast-sell floor, whatever its quality band says.

## Sell plan

Only items in your **Sell categories** are pulled and priced; everything else is never listed. The default is:

- unequipped weapons (Primary, Secondary, Melee) and armor (Defensive),
- general loot (Clothing, Collectible, Jewelry, Other, Tool, Material, Car),
- museum items (Plushie, Flower, Artifact).

Drugs, medical items, boosters, energy drinks, alcohol, candy, supply packs, books and temporary weapons are kept unless you add them.

Each item gets a plan automatically. The **Sell plan** tab lets you change it, and your changes are saved in `~/.tma_config.json`:

| Plan | When (defaults) | List price |
|---|---|---|
| **Dump** | worth < $20k each and the stack < $1M | 5% under the cheapest listing |
| **Competitive** | worth ≥ $20k each, or a stack ≥ $1M | cheapest listing − $1 |
| **Top dollar** | a weapon/armor copy with quality ≥ 50, or a Competitive item with < 20 listings | Greedy (upper quartile) |
| **Shop** | a city shop pays at least the market price after Torn's 5% sales tax | the shop's price; not listed |
| **Keep** | only when you choose it | never sold |

Every threshold can be changed under **Rules**. **Est. proceeds** is after the 5% item-market tax.

## Keeping what you need

Only the surplus is ever sold. The **Keeping** column shows how many of each item stay in your inventory, and **Selling** shows the rest. Type a number in **Keeping** to set it by hand; that number is saved. The defaults:

- **Crime items are all kept.** That covers everything an Organized Crime requires (from Torn's `torn/organizedcrimes`) and everything whose in-game description says a crime uses it (Forgery, Disposal, Graffiti, Arson…). Untick **Keep all crime items** under Rules to keep only a stock and sell the rest: 1 of each reusable OC tool, 3 of each item an OC uses up, and 1 of every other crime item.
- **Clothing and Collectibles:** 1 of each is kept, so only duplicates are sold. Change the categories under Rules.
- **Weapons and armor:** the copies kept are your best by quality. Equipped gear is never touched.
- **Museum set pieces:** when a complete set is worth more exchanged for points than sold (see [Museum sets](#museum-sets)), one of each piece per complete set is kept. Extras still sell.
- **Everything else:** 0 kept.

## Shop run

Shop-plan items are sold to city shops with one button. **Start shop run →** on the Sell plan tab opens the first shop, and the userscript puts one green button in the same spot on every shop page:

1. **Review:** the panel under the button lists each item × quantity × price and the total. Click a line to leave it out.
2. **Sell N item(s) here · $X:** fills only this run's Shop items (weapon/armor copies are matched by their armoryID; on Big Al's it ticks each copy) and opens Torn's *Are you sure?* with the total.
3. **Confirm: sell …:** confirms that one sale.
4. **Next: <shop> →:** goes to the next shop, until **Shop run done ✓**.

Each shop page also reports what that shop really buys back to the app (`POST 127.0.0.1:9999/shop-report`, stored in `data/shop_buys.json`). The catalog's vendor is only a guess; Big Al's, for example, buys only a few of the weapons it stocks. Later runs send items only to shops seen buying them, and anything no shop takes falls back to its market plan.

## Listing on the Item Market

**Open Add Listing →** on the Sell plan tab opens Torn's Add Listing page, where the userscript puts one green button in the same spot:

1. **Review:** the panel lists every planned listing (item × quantity × price) and the total. Click a line to leave it out. If Torn still has selections from an earlier visit (Torn keeps them across reloads), the panel warns you to clear them first.
2. **Fill N item(s) for listing:** goes tab by tab through the categories that hold planned items, lets Torn load each full list, and fills it:
   - stacks get quantity = **Selling** and the list price;
   - weapon/armor copies get **Select** and a price from that copy's own rolls. Good rolls go to Top dollar, and no copy lists below the item's floor.

   Held back, with the reason shown: Keep items, crime items, your best copies, copies routed to a shop, and anything a price guard stops. The *anonymous (+10% fee)* box is never touched. Click the status box to see every row filled and every row held.
3. **Add N listing(s) to market · $X:** the total is Torn's own, read from the page. The button presses Torn's ADD TO MARKET, and turns into **Confirm** if Torn asks.

A full plan of about 650 rows across 13 tabs fills in roughly 3½ minutes. **Start over** clears all of Torn's selections.

## Your listings

Each Run also reads your active listings (`user/itemmarket`) and checks them against the current market. That includes items you no longer hold, which cost one extra market call each. The **Your listings** section on the Sell plan tab shows each one's price, the cheapest other listing, how many listings are cheaper, and a status:

| Status | New price |
|---|---|
| **undercut by $X** | $1 under the cheapest other listing |
| **tied for cheapest** | $1 under |
| **cheapest by N%** (more than 10% under the next listing) | what you could ask: $1 under the next listing |
| **cheapest**, **only listing** | none |

Your own listing is left out of the comparison. Weapon/armor copies only compete with copies of the same quality or better. A new price is never suggested below what a shop pays (after the tax) or half Torn's reference price. **Open your listings →** goes to Torn's page, where you change prices and click **SAVE CHANGES**. A one-button fill for that page is planned.

## Museum sets

Each Run reads the museum sets (`torn/museum`, refreshed daily) and the cheapest point price (`market/pointsmarket`). For every set you own a piece of, the **Museum sets** section on the Sell plan tab compares:

- **Exchanged:** the set's points × the point price;
- **Sold:** every piece at its fast-sell price after the 5% tax (Torn's reference price for pieces you don't own).

When exchanging wins, the pieces of your complete sets are held out of the sell plan, ready to trade in at the museum. **Next set needs** lists what one more set is missing and its cost at reference prices.

## Price history

Every Run adds one row per priced item to `data/price_history.csv`: time, item, category, floor/fast/fair/greedy prices, number of listings, units listed, your quantity and Torn's reference price. Items you own and items you have listed are always recorded. **Track price history for** (next to Sell categories) adds every item in the chosen categories, owned or not, at one extra market call each. View the rows under **Diagnostics → Price history**, or open the CSV in a spreadsheet.

## App tabs

| Tab | Contents |
|---|---|
| **Sell plan** | Per-item plan, Keeping/Selling, list price, estimated proceeds, Rules, the shop run and Add Listing links, your listings and museum sets. |
| **Pricing table** | Fast/fair/greedy prices, copy-level prices from your stats, and a plain-text listing builder. |
| **Special finds** | Weapon/armor copies priced well above the item's standard curve. |
| **Autofill script** | Install link and download for the Tampermonkey userscript. |
| **Diagnostics** | Parsed and unmatched items, raw market data, summary tables, the copy stats cache (with when each copy was first seen) and price history. |

## Torn API key & privacy

| | |
|---|---|
| **Key access level** | **Limited**: inventory pulls need `user → inventory`, and your listings need a Limited key. Market and item lookups work with any key. |
| **What it calls** | `user/inventory`, `torn/{uid}?selections=itemdetails`, `user/itemmarket`, `market/{id}/itemmarket`, `market/pointsmarket`, `torn/items`, `torn/organizedcrimes`, `torn/museum`. All read-only. |
| **Key storage** | Only on your PC, in `~/.tma_config.json`, and only if **Remember API key** is ticked. |
| **Data sharing** | None. Requests go straight from your PC to `api.torn.com`. The userscript talks only to Torn and to this app on `127.0.0.1` (your own machine). |
| **Purpose** | Pricing and selling your own items. |

TornHoarder never buys, sells or lists anything by itself. The userscript fills in fields and shows one button, and each sale or listing happens only when you click it: one click, one action, in line with Torn's scripting rules.

## Project structure

```
TornHoarder/
├─ app/
│  ├─ streamlit_app.py       # input form + pipeline orchestration
│  ├─ paths.py               # shared path constants
│  └─ ui/                    # one results tab per module
│     ├─ results.py          # price overview + tab layout
│     ├─ sell_plan.py        # plans, Keeping/Selling, Rules, shop run + Add Listing links
│     ├─ pricing_table.py    # price suggestions + listing-text builder
│     ├─ special_finds.py    # copies priced above the item's standard curve
│     ├─ autofill_export.py  # userscript generation, install link, update server start
│     ├─ diagnostics.py      # parsed/unmatched/market/summary tables
│     └─ format.py
├─ src/
│  └─ tma/
│     ├─ http_client.py      # rate limiting, retries, Torn API error handling
│     ├─ inventory.py        # user/inventory fetch, copy stats + stats cache
│     ├─ inventory_matcher.py
│     ├─ market_fetch.py     # item-market listings fetch (concurrent)
│     ├─ market_enrichment.py
│     ├─ sell_plan.py        # plan classifier, reserves, price guards (pure logic)
│     ├─ shops.py            # city-shop routing + learned shop buy lists
│     ├─ crime_items.py      # OC requirements + crime-item detection
│     ├─ museum.py           # museum sets: exchange vs sell
│     ├─ my_listings.py      # your active listings vs the market
│     ├─ price_history.py    # one CSV row per item per run
│     ├─ catalog.py          # torn/items dictionary + profiles, auto-refresh
│     ├─ item_stats.py       # stat-value model / quality-curve fitting
│     ├─ quality.py
│     ├─ userscript.py       # Tampermonkey script template (shop run + Add Listing)
│     ├─ autoupdate_server.py # serves the script, receives shop reports
│     ├─ config.py           # local settings: API key, plans, rules, reserves
│     └─ __init__.py         # __version__
├─ data/
│  ├─ torn_item_dictionary.csv
│  └─ torn_item_profiles.json  # (generated, git-ignored: tornhoarder.user.js, shop_buys.json,
│                              #  oc_items.json, uid_stats.json, museum_sets.json, price_history.csv)
├─ tools/
│  ├─ server.ps1             # start/stop the app on Windows
│  ├─ update_server.py       # serve the script without the app running
│  ├─ dictionary_v2.py
│  └─ calibrate_model.py
├─ tests/
├─ requirements.txt
└─ README.md
```

## Tests

```bash
python -m pytest
```

The userscript tests run the generated script in Node to check the listing decisions; they're skipped if `node` isn't installed.

## Regenerate the item dictionary

The app refreshes `data/torn_item_dictionary.csv` and `data/torn_item_profiles.json` from `torn/items` on its own once they're more than 24h old. To rebuild them by hand:

```bash
python tools/dictionary_v2.py
```

The script writes `tools/output/torn_items_v2.json`, `tools/output/torn_item_profiles.json` and `tools/output/torn_item_dictionary.csv`; copy the CSV and profiles JSON into `data/`.
