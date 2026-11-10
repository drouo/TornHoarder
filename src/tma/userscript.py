from __future__ import annotations

import json
from typing import Dict, Optional

TEMPLATE = """\
// ==UserScript==
// @name         TornHoarder
// @namespace    tornhoarder
// @version      1.0.0
// @description  TornHoarder: fills Add Listing prices from your sell plan and runs one-button city shop sales.
// @match        https://www.torn.com/page.php?sid=ItemMarket*
// @match        https://www.torn.com/shops.php*
// @match        https://www.torn.com/bigalgunshop.php*
// @updateURL    http://127.0.0.1:9999/tornhoarder.user.js
// @downloadURL  http://127.0.0.1:9999/tornhoarder.user.js
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
    "use strict";

    var PRICE_MAP = __PRICE_MAP__;
    var BASE_STATS = __BASE_STATS__;
    var CFG = __CFG__;
    var SHOP = __SHOP__;

    var NAME_TO_ID = {};
    Object.keys(PRICE_MAP).forEach(function (itemId) {
        NAME_TO_ID[PRICE_MAP[itemId].name] = parseInt(itemId, 10);
        PRICE_MAP[itemId].fast = Number(PRICE_MAP[itemId].fast);
        PRICE_MAP[itemId].fair = Number(PRICE_MAP[itemId].fair);
        PRICE_MAP[itemId].greedy = Number(PRICE_MAP[itemId].greedy);
    });

    var NAME_LOOKUP = {};
    Object.keys(PRICE_MAP).forEach(function (itemId) {
        var nm = PRICE_MAP[itemId].name;
        NAME_LOOKUP[nm] = parseInt(itemId, 10);
        NAME_LOOKUP[nm.toLowerCase()] = parseInt(itemId, 10);
    });

    var VER = (typeof GM_info !== "undefined" && GM_info.script)
        ? GM_info.script.version
        : "1.0.0";
    var STATS = { rows: 0, filled: 0, skipped: 0, held: 0, unmatched: 0 };
    var PLAN_BASIS = { dump: "fast", competitive: "fast", top: "greedy" };
    var UNKNOWN = {};
    var HUD = null;
    var HUD_TEXT = "";
    var scanning = false;

    function ensureHud() {
        if (HUD && HUD.parentNode) {
            return HUD;
        }
        HUD = document.createElement("div");
        HUD.id = "tma-hud";
        HUD.style.cssText =
            "position:fixed;right:8px;bottom:8px;z-index:999999;" +
            "font:11px/1.4 monospace;color:#7CFC00;background:rgba(0,0,0,.85);" +
            "border:1px solid #555;border-radius:4px;padding:4px 8px;max-width:460px;" +
            "pointer-events:none;white-space:nowrap;";
        document.body.appendChild(HUD);
        return HUD;
    }

    function paintHud() {
        var text =
            "TornHoarder " + VER + " | items " + Object.keys(PRICE_MAP).length +
            " | rows " + STATS.rows + " | filled " + STATS.filled +
            " | skipped " + STATS.skipped + " | shop/keep " + STATS.held + " | unmatched " + STATS.unmatched;
        if (text !== HUD_TEXT) {
            ensureHud().textContent = text;
            HUD_TEXT = text;
        }
    }

    function matchId(name) {
        if (!name) {
            return null;
        }
        var id = NAME_LOOKUP[name];
        if (id == null) {
            id = NAME_LOOKUP[name.toLowerCase()];
        }
        return id != null ? id : null;
    }

    function money(value) {
        var n = parseFloat(String(value || "").replace(/[^0-9.]/g, ""));
        return isFinite(n) ? n : NaN;
    }

    function rowName(container) {
        var el = container.querySelector("[data-item]");
        if (el && el.getAttribute("data-item")) {
            return el.getAttribute("data-item").trim();
        }
        var nm = container.querySelector(".item-name, [class*='item-name']");
        if (nm) {
            return nm.textContent.trim();
        }
        return null;
    }

    function rowInputs(container) {
        var price = container.querySelector(
            "input.input-money, input[name*='price'], input[id*='price'], input[class*='price']"
        );
        var qty = container.querySelector(
            "input[name*='amount'], input[id*='amount'], input[class*='amount']"
        );
        return { price: price, qty: qty };
    }

    function rowRolls(container) {
        var text = (container.textContent || "").replace(/\\u00a0/g, " ").toLowerCase();
        var pts = { damage: 0, accuracy: 0, armor: 0 };
        var found = false;
        ["damage", "accuracy", "armor"].forEach(function (key) {
            var m = text.match(new RegExp(key + ":?\\\\s*([0-9.]+)"));
            if (m) {
                pts[key] = parseFloat(m[1]);
                found = true;
            }
        });
        return found ? pts : null;
    }

    function premiumFor(quality) {
        if (quality < CFG.premiumThreshold) {
            return 1.0;
        }
        var factor = Math.max(quality, CFG.premiumThreshold) / 100.0;
        return Math.min(1.0 + factor * 2.0, CFG.premiumMaxMult);
    }

    function basisIndex(basisName) {
        return basisName === "fast" ? 1 : basisName === "greedy" ? 3 : 2;
    }

    function interpolate(curve, q, basisName) {
        if (!curve || !curve.length || q == null) {
            return null;
        }
        var idx = basisIndex(basisName);
        if (q <= curve[0][0]) {
            return curve[0][idx];
        }
        var last = curve[curve.length - 1];
        if (q >= last[0]) {
            return last[idx];
        }
        for (var i = 0; i < curve.length - 1; i++) {
            var a = curve[i];
            var b = curve[i + 1];
            if (a[0] <= q && q <= b[0]) {
                if (b[0] === a[0]) {
                    return b[idx];
                }
                if (a[idx] == null || b[idx] == null) {
                    return null;
                }
                var t = (q - a[0]) / (b[0] - a[0]);
                var pv = Math.round(a[idx] + (b[idx] - a[idx]) * t);
                return pv > 0 ? pv : null;
            }
        }
        return null;
    }

    function qualityFromFit(fit, rolls) {
        if (!fit || !fit.type || !rolls) {
            return null;
        }
        var base = fit.base || {};
        var coef = fit.coef || {};
        if (fit.type === "Weapon") {
            var q = Number(fit.intercept || 0);
            var any = false;
            if (rolls.damage != null && base.damage != null) {
                q += coef.damage * (rolls.damage - base.damage);
                any = true;
            }
            if (rolls.accuracy != null && base.accuracy != null) {
                q += coef.accuracy * (rolls.accuracy - base.accuracy);
                any = true;
            }
            return any ? q : null;
        }
        if (fit.type === "Armor" && rolls.armor != null) {
            if (fit.intercept != null && base.armor != null) {
                return Number(fit.intercept) + coef.armor * (rolls.armor - base.armor);
            }
            if (base.armor != null) {
                return (rolls.armor - base.armor) * 20;
            }
        }
        return null;
    }

    function processRow(container) {
        if (container.getAttribute("data-tma-filled") || container.getAttribute("data-tma-skipped")) {
            return;
        }
        var name = rowName(container);
        var itemId = name ? matchId(name) : null;
        var entry = itemId != null ? PRICE_MAP[itemId] : null;
        var inputs = rowInputs(container);
        var rolls = rowRolls(container);
        var base = itemId != null ? BASE_STATS[itemId] : null;

        if (entry && !inputs.price) {
            return;
        }

        if (!entry) {
            if (name && itemId == null && !UNKNOWN[name]) {
                UNKNOWN[name] = true;
                console.log("[TornHoarder] no price data for row: " + name);
            }
            STATS.unmatched += 1;
            container.setAttribute("data-tma-skipped", "1");
            paintHud();
            return;
        }

        var quality = null;
        var qualitySource = null;
        if (entry.fit && rolls) {
            quality = qualityFromFit(entry.fit, rolls);
            qualitySource = "fit";
        } else if (base && rolls) {
            quality =
                base.type === "Weapon"
                    ? (rolls.damage - base.damage + (rolls.accuracy - base.accuracy)) * 10
                    : (rolls.armor - base.armor) * 20;
            qualitySource = "base";
        }

        // Sell plan from the app; an automatic plan is bumped to "top" for a
        // high-roll copy, since the app can't tell which row is which copy.
        var plan = entry.plan || null;
        var listPrice = entry.list;
        var shopMin = entry.shopMin;
        var refMin = entry.refMin;
        if (plan === "shop" && entry.fallback) {
            // Still in inventory, so no shop took it (or the shop run hasn't
            // been done): list it with its market plan, without the shop floor.
            plan = entry.fallback.plan;
            listPrice = entry.fallback.list;
            shopMin = null;
            refMin = entry.fallback.refMin;
        }
        if (plan && plan !== "keep" && entry.auto !== false && quality != null && quality >= CFG.topMinQuality) {
            plan = "top";
        }
        if (plan === "keep" || plan === "shop") {
            STATS.held += 1;
            container.setAttribute("data-tma-skipped", "1");
            container.style.outline = "1px dashed #888";
            paintHud();
            return;
        }
        var basis = plan ? PLAN_BASIS[plan] : CFG.priceBasis;

        var curvePrice = entry.curve && quality != null
            ? interpolate(entry.curve, quality, basis)
            : null;
        if (curvePrice != null && plan === "dump") {
            curvePrice = Math.max(Math.round(curvePrice * (1 - CFG.dumpUndercutPct / 100)), 1);
        }
        var basePrice = listPrice != null ? listPrice : (entry[basis] || null);
        var price = curvePrice != null && curvePrice > 0 ? curvePrice : basePrice;
        if (!plan && price != null && quality != null && quality >= CFG.premiumThreshold && curvePrice == null) {
            price = Math.max(price, Math.round(price * premiumFor(quality)));
        }
        // Same guards as the app: never list under what a shop pays after
        // tax, and leave anything under half Torn's reference price for a
        // manual look (a stray $1 listing can drag a curve bucket down).
        if (price != null && shopMin) {
            price = Math.max(price, shopMin);
        }
        if (price != null && refMin && price < refMin) {
            console.log("[TornHoarder] held " + entry.name + " @" + price + ": under refMin " + refMin);
            STATS.held += 1;
            container.setAttribute("data-tma-skipped", "1");
            container.style.outline = "2px dashed #e53935";
            paintHud();
            return;
        }
        if (price != null && quality != null && quality >= CFG.premiumThreshold) {
            container.style.outline = "2px solid #ffd97a";
        }

        if (price != null && inputs.price && !inputs.price.value) {
            inputs.price.value = String(price);
            STATS.filled += 1;
            container.setAttribute("data-tma-filled", "1");
            console.log("[TornHoarder] filled " + entry.name + " @" + price +
                " (q=" + (quality != null ? quality.toFixed(2) : "n/a") + ", " + qualitySource + ")");
        } else {
            STATS.skipped += 1;
            container.setAttribute("data-tma-skipped", "1");
        }
        if (price != null && CFG.fillQty && inputs.qty && !inputs.qty.value) {
            inputs.qty.value = String(entry.qty || 1);
        }
        paintHud();
        window.__TMA__ = { version: VER, items: Object.keys(PRICE_MAP).length, stats: STATS };
    }

    function extract() {
        if (document.hidden || scanning || !document.body) {
            return;
        }
        scanning = true;
        try {
            var containers = [];
            var list = document.querySelectorAll("[data-item]");
            for (var i = 0; i < list.length; i++) {
                var row = list[i].closest(".torn-item, li, tr, [class*='listing-item']");
                if (row && containers.indexOf(row) === -1) {
                    containers.push(row);
                }
            }
            STATS.rows = containers.length;
            for (var j = 0; j < containers.length; j++) {
                processRow(containers[j]);
            }
            window.__TMA__ = { version: VER, items: Object.keys(PRICE_MAP).length, stats: STATS };
            paintHud();
        } finally {
            scanning = false;
        }
    }

    // ---- Shop run: one fixed button, one action per click -----------------
    // Click 1 on a shop page fills the amounts for this run's Shop items and
    // presses Torn's own SELL ITEMS (a single sale). If Torn asks to confirm,
    // the same button confirms. When nothing of ours is left on the page it
    // becomes "Next shop". It never fills an item that isn't in SHOP.items,
    // and weapon/armor copies only by their armoryID.
    function shopMode() {
        // shops.php?step=pharmacy -> "pharmacy"; bigalgunshop.php -> "bigalgunshop"
        var stepMatch = /[?&]step=([a-z]+)/.exec(location.search);
        var step = stepMatch ? stepMatch[1] : location.pathname.replace(/^[/]|[.]php$/g, "");
        var route = SHOP.route || [];
        var busyUntil = 0;
        var note = "";

        var btn = document.createElement("button");
        btn.id = "tma-shop-btn";
        btn.type = "button";
        btn.style.cssText =
            "position:fixed;left:50%;bottom:72px;transform:translateX(-50%);z-index:999999;" +
            "min-width:340px;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);";
        document.body.appendChild(btn);

        function ourRows() {
            var rows = [];
            var ids = document.querySelectorAll("input[type=hidden][name=ID]");
            for (var i = 0; i < ids.length; i++) {
                var li = ids[i].closest("li");
                var want = SHOP.items[ids[i].value];
                if (!li || !want) {
                    continue;
                }
                var armory = li.querySelector("input[name=armoryID]");
                if (want.uids && (!armory || want.uids.indexOf(armory.value) === -1)) {
                    continue;
                }
                // Most shops: an amount box per item. Big Al's: a checkbox per copy.
                var box = li.querySelector("input.input-money:not([type=hidden]), input[type=checkbox][name=amount]");
                if (!box) {
                    continue;
                }
                var have = box.type === "checkbox" ? 1 : parseInt(box.getAttribute("data-money"), 10) || 0;
                var qty = want.uids ? have : Math.min(have, want.qty);
                if (qty > 0) {
                    rows.push({ li: li, box: box, qty: qty, price: Number(want.price) || 0 });
                }
            }
            return rows;
        }

        function setAmount(row) {
            if (row.box.type === "checkbox") {
                if (!row.box.checked) {
                    row.box.click();
                }
                row.li.style.outline = "2px solid #43a047";
                return;
            }
            row.box.value = String(row.qty);
            ["input", "keyup", "change"].forEach(function (ev) {
                row.box.dispatchEvent(new Event(ev, { bubbles: true }));
            });
            var hidden = row.li.querySelector("input[type=hidden][name=amount]");
            if (hidden) {
                hidden.value = String(row.qty);
            }
            row.li.style.outline = "2px solid #43a047";
        }

        // The page's own SELL ITEMS, not the copy inside the (hidden) confirm box.
        function sellButton() {
            var all = document.querySelectorAll("button");
            for (var i = 0; i < all.length; i++) {
                var b = all[i];
                if (b.offsetParent === null || b.closest(".sell-confirm")) {
                    continue;
                }
                if (/^\\s*SELL ITEMS\\s*$/i.test(b.textContent || "")) {
                    return b;
                }
            }
            return null;
        }

        // After SELL ITEMS, Torn swaps in "Are you sure you would like to sell
        // these items? Total value: $X  [SELL ITEMS] Cancel" and reuses a
        // SELL ITEMS button for it, so the prompt is found by its wording.
        function confirmButton() {
            var all = document.querySelectorAll("button, a, input[type=button], input[type=submit]");
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if (el === btn || el.offsetParent === null) {
                    continue;
                }
                var text = (el.textContent || el.value || "").trim();
                if (!/^(yes|confirm|sell items)$/i.test(text)) {
                    continue;
                }
                // Torn: button > span.btn > span.btn-wrap > div.sell-confirm
                var box = el.closest(".sell-confirm") || el;
                for (var up = 0; up < 4 && box && !/are you sure/i.test(box.textContent || ""); up++) {
                    box = box.parentElement;
                }
                if (box && /are you sure/i.test(box.textContent || "")) {
                    return el;
                }
            }
            return null;
        }

        function nextShop() {
            var idx = -1;
            for (var i = 0; i < route.length; i++) {
                if (route[i].step === step) {
                    idx = i;
                }
            }
            return route[idx + 1] || null;
        }

        function money(n) {
            return "$" + Math.round(n).toLocaleString("en-US");
        }

        function render() {
            if (Date.now() < busyUntil) {
                btn.textContent = "Selling…";
                return;
            }
            var rows = ourRows();
            if (rows.length) {
                var n = 0;
                var total = 0;
                rows.forEach(function (r) {
                    n += r.qty;
                    total += r.qty * r.price;
                });
                var what = n.toLocaleString("en-US") + " item(s) · " + money(total);
                btn.textContent = confirmButton() ? "Confirm: sell " + what : "Sell " + what + " here";
            } else {
                var nxt = nextShop();
                btn.textContent = nxt
                    ? "Next: " + nxt.name + " (" + nxt.items.toLocaleString("en-US") + ") →"
                    : "Shop run done ✓";
            }
            if (note) {
                btn.textContent += " — " + note;
            }
        }

        btn.addEventListener("click", function () {
            if (Date.now() < busyUntil) {
                return;
            }
            note = "";
            var c = confirmButton();
            if (c) {
                busyUntil = Date.now() + 2500;
                c.click();
                render();
                return;
            }
            var rows = ourRows();
            if (rows.length) {
                rows.forEach(setAmount);
                var sell = sellButton();
                if (!sell || sell.disabled) {
                    note = "amounts filled, press SELL ITEMS yourself";
                    render();
                    return;
                }
                busyUntil = Date.now() + 1200;
                sell.click();
                render();
                return;
            }
            var nxt = nextShop();
            if (nxt) {
                location.href = nxt.url;
            }
        });

        // Tell the app what this shop's Sell page really lists, once per page
        // load and before anything is sold: items shown = it buys them;
        // items we routed here that aren't shown = it doesn't.
        function reportShop() {
            var ids = document.querySelectorAll("input[type=hidden][name=ID]");
            if (!ids.length || typeof GM_xmlhttpRequest === "undefined") {
                return false;
            }
            var buys = {};
            for (var i = 0; i < ids.length; i++) {
                buys[ids[i].value] = true;
            }
            var rejects = Object.keys(SHOP.items).filter(function (id) {
                return SHOP.items[id].step === step && !buys[id];
            });
            var here = route.filter(function (r) { return r.step === step; })[0];
            GM_xmlhttpRequest({
                method: "POST",
                url: CFG.reportUrl,
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify({
                    step: step,
                    name: here ? here.name : document.title.replace(/ [|] TORN$/, ""),
                    buys: Object.keys(buys).map(Number),
                    rejects: rejects.map(Number)
                })
            });
            console.log("[TornHoarder] reported " + step + ": buys " + Object.keys(buys).length + ", refused " + rejects.length);
            return true;
        }

        var reported = false;
        render();
        setInterval(function () {
            if (!reported) {
                reported = reportShop();
            }
            render();
        }, 700);
        console.log("[TornHoarder] shop run v" + VER + ": " + route.length + " shop(s) on route");
    }

    if (window.top !== window) {
        return;
    }

    if (location.pathname.indexOf("/shops.php") === 0 || location.pathname.indexOf("/bigalgunshop.php") === 0) {
        if (SHOP.route && SHOP.route.length) {
            shopMode();
        }
        return;
    }

    console.log("[TornHoarder] autofill v" + VER + " loaded, " + Object.keys(PRICE_MAP).length + " items priced");
    setTimeout(extract, 2500);
    var timer = setInterval(extract, Number(CFG.scanMs) || 1500);
    window.addEventListener("beforeunload", function () {
        clearInterval(timer);
    });
})();
"""

DEFAULT_CFG: Dict[str, object] = {
    "priceBasis": "fair",
    "fillQty": True,
    "premiumThreshold": 50.0,
    "premiumMaxMult": 3.0,
    "dumpUndercutPct": 5.0,
    "topMinQuality": 50.0,
    "scanMs": 1500,
    "reportUrl": "http://127.0.0.1:9999/shop-report",
}


def build_userscript(
    price_map: Dict[int, Dict[str, object]],
    base_stats: Dict[int, Dict[str, object]],
    cfg: Optional[Dict[str, object]] = None,
    shop_run: Optional[Dict[str, object]] = None,
) -> str:
    merged_cfg = {**DEFAULT_CFG, **(cfg or {})}
    return (
        TEMPLATE.replace("__PRICE_MAP__", json.dumps({str(k): v for k, v in price_map.items()}))
        .replace("__BASE_STATS__", json.dumps({str(k): v for k, v in base_stats.items()}))
        .replace("__CFG__", json.dumps(merged_cfg))
        .replace("__SHOP__", json.dumps(shop_run or {"route": [], "items": {}}))
    )