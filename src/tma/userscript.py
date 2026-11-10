from __future__ import annotations

import json
from typing import Dict, Optional

TEMPLATE = """\
// ==UserScript==
// @name         TornHoarder
// @namespace    tornhoarder
// @version      1.1.0
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
        : "1.1.0";
    var STATS = { rows: 0, filled: 0, skipped: 0, held: 0, unmatched: 0 };
    // What was filled / held on this page, for the review list behind the status box.
    var FILLED = [];
    var HELD = [];
    var DETAILS = null;
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
            "cursor:pointer;white-space:nowrap;";
        HUD.title = "Click for the list of what was filled and held";
        HUD.addEventListener("click", toggleDetails);
        document.body.appendChild(HUD);
        return HUD;
    }

    function money(n) {
        return "$" + Math.round(n).toLocaleString("en-US");
    }

    function filledTotal() {
        return FILLED.reduce(function (sum, f) { return sum + f.qty * f.price; }, 0);
    }

    // Review list: every row filled (qty x price) and every row held, with why.
    function toggleDetails() {
        if (DETAILS && DETAILS.parentNode) {
            DETAILS.parentNode.removeChild(DETAILS);
            DETAILS = null;
            return;
        }
        DETAILS = document.createElement("div");
        DETAILS.style.cssText =
            "position:fixed;right:8px;bottom:34px;z-index:999999;max-height:60vh;overflow:auto;" +
            "font:11px/1.5 monospace;color:#ddd;background:rgba(0,0,0,.92);border:1px solid #555;" +
            "border-radius:4px;padding:6px 10px;min-width:360px;white-space:pre;";
        var lines = ["Filled " + FILLED.length + " row(s) · " + money(filledTotal()) + " before tax"];
        FILLED.forEach(function (f) {
            lines.push("  " + f.qty.toLocaleString("en-US") + " × " + f.name + " @ " + money(f.price) + " = " + money(f.qty * f.price));
        });
        lines.push("", "Held " + HELD.length + " row(s)");
        HELD.forEach(function (h) {
            lines.push("  " + h.name + " — " + h.reason);
        });
        DETAILS.textContent = lines.join(String.fromCharCode(10));
        document.body.appendChild(DETAILS);
    }

    function paintHud() {
        var text =
            "TornHoarder " + VER + " | filled " + STATS.filled + " · " + money(filledTotal()) +
            " | held " + STATS.held + " | skipped " + STATS.skipped + " | unmatched " + STATS.unmatched + "  ▸ list";
        if (text !== HUD_TEXT) {
            ensureHud().textContent = text;
            HUD_TEXT = text;
        }
    }

    function hold(container, entry, reason, outline) {
        STATS.held += 1;
        HELD.push({ name: entry.name, reason: reason });
        container.setAttribute("data-tma-skipped", "1");
        container.style.outline = outline || "1px dashed #888";
        paintHud();
    }

    // Set a field the way typing would, so the page's own code sees the change.
    function setField(input, value) {
        var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        setter.call(input, String(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
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
        // Weapon/armor copies you're keeping: rows don't carry copy ids here,
        // so hold every row at least as good as the worst kept copy.
        if (entry.keepAll || (entry.keepMinQuality != null && (quality == null || quality >= entry.keepMinQuality))) {
            hold(container, entry, "keeping your best copy");
            return;
        }
        if (plan && plan !== "keep" && entry.auto !== false && quality != null && quality >= CFG.topMinQuality) {
            plan = "top";
        }
        if (plan === "keep" || plan === "shop") {
            hold(container, entry, plan === "keep" ? "Keep" : "sell to a shop");
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
            hold(container, entry, money(price) + " is under half Torn's reference price — check the market", "2px dashed #e53935");
            return;
        }
        if (price != null && quality != null && quality >= CFG.premiumThreshold) {
            container.style.outline = "2px solid #ffd97a";
        }

        var qty = entry.curve ? 1 : (entry.qty || 1);
        if (price != null && inputs.price && !inputs.price.value) {
            setField(inputs.price, price);
            STATS.filled += 1;
            FILLED.push({ name: entry.name, qty: qty, price: price });
            container.setAttribute("data-tma-filled", "1");
            console.log("[TornHoarder] filled " + entry.name + " x" + qty + " @" + price +
                " (q=" + (quality != null ? quality.toFixed(2) : "n/a") + ", " + qualitySource + ")");
            // Always set quantity: Torn may pre-fill your whole stack, but only
            // the Selling part (owned minus what you keep) should be listed.
            if (CFG.fillQty && inputs.qty) {
                setField(inputs.qty, qty);
            }
        } else {
            STATS.skipped += 1;
            container.setAttribute("data-tma-skipped", "1");
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

        // Review list above the button: what click 1 will fill, line by line.
        // Clicking a line leaves that item out of this shop's sale (and back in).
        var excluded = {};
        var panelSig = "";
        var panel = document.createElement("div");
        panel.id = "tma-shop-review";
        panel.style.cssText =
            "position:fixed;left:50%;bottom:136px;transform:translateX(-50%);z-index:999999;" +
            "min-width:340px;max-height:40vh;overflow:auto;padding:8px 12px;border-radius:8px;" +
            "background:rgba(0,0,0,.9);border:1px solid #2e7d32;color:#eee;font:13px/1.5 Arial,sans-serif;";
        document.body.appendChild(panel);

        function renderPanel(allRows, confirming) {
            if (!allRows.length || confirming) {
                panel.style.display = "none";
                panelSig = "";
                return;
            }
            var byId = {};
            var order = [];
            allRows.forEach(function (r) {
                if (!byId[r.id]) {
                    byId[r.id] = { id: r.id, name: r.name, qty: 0, price: r.price };
                    order.push(r.id);
                }
                byId[r.id].qty += r.qty;
            });
            var sig = order.map(function (id) { return id + ":" + byId[id].qty + ":" + !!excluded[id]; }).join("|");
            panel.style.display = "block";
            if (sig === panelSig) {
                return;
            }
            panelSig = sig;
            panel.textContent = "";
            var head = document.createElement("div");
            head.textContent = "Will sell here (click a line to leave it out):";
            head.style.cssText = "color:#9ccc65;margin-bottom:4px;";
            panel.appendChild(head);
            order.forEach(function (id) {
                var g = byId[id];
                var line = document.createElement("div");
                line.textContent = (excluded[id] ? "✕ " : "✓ ") + g.qty.toLocaleString("en-US") + " × " + g.name +
                    " @ " + money(g.price) + " = " + money(g.qty * g.price);
                line.style.cssText = "cursor:pointer;" + (excluded[id] ? "text-decoration:line-through;color:#888;" : "");
                line.addEventListener("click", function () {
                    excluded[id] = !excluded[id];
                    render();
                });
                panel.appendChild(line);
            });
        }

        function ourRows(includeExcluded) {
            var rows = [];
            var ids = document.querySelectorAll("input[type=hidden][name=ID]");
            for (var i = 0; i < ids.length; i++) {
                var li = ids[i].closest("li");
                var want = SHOP.items[ids[i].value];
                if (!li || !want || (excluded[ids[i].value] && !includeExcluded)) {
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
                    rows.push({
                        id: ids[i].value, name: want.name || ("item " + ids[i].value),
                        li: li, box: box, qty: qty, price: Number(want.price) || 0
                    });
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
            var confirming = !!confirmButton();
            renderPanel(ourRows(true), confirming);
            var rows = ourRows();
            if (rows.length) {
                var n = 0;
                var total = 0;
                rows.forEach(function (r) {
                    n += r.qty;
                    total += r.qty * r.price;
                });
                var what = n.toLocaleString("en-US") + " item(s) · " + money(total);
                btn.textContent = confirming ? "Confirm: sell " + what : "Sell " + what + " here";
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