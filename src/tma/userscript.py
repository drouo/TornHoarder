from __future__ import annotations

import json
import time
from typing import Dict, Optional

from . import __version__

TEMPLATE = """\
// ==UserScript==
// @name         TornHoarder
// @namespace    tornhoarder
// @version      __VERSION__
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

    Object.keys(PRICE_MAP).forEach(function (itemId) {
        PRICE_MAP[itemId].fast = Number(PRICE_MAP[itemId].fast);
        PRICE_MAP[itemId].fair = Number(PRICE_MAP[itemId].fair);
        PRICE_MAP[itemId].greedy = Number(PRICE_MAP[itemId].greedy);
    });

    var VER = (typeof GM_info !== "undefined" && GM_info.script)
        ? GM_info.script.version
        : "__VERSION__";
    var PLAN_BASIS = { dump: "fast", competitive: "fast", top: "greedy" };
    var NL = String.fromCharCode(10);

    function fmtMoney(n) {
        return "$" + Math.round(n).toLocaleString("en-US");
    }

    // Several installed copies each draw their own button in the same spot, so
    // the one on top may be stale. Warn from inside the panel.
    function copiesWarning(panelEl, buttonId) {
        var n = document.querySelectorAll("#" + buttonId).length;
        if (n < 2) {
            return;
        }
        var div = document.createElement("div");
        div.textContent = n + " copies of TornHoarder are running on this page, and the button on top may be an old one. " +
            "In the Tampermonkey dashboard keep only one TornHoarder script (v" + VER + ") and delete the rest.";
        div.style.cssText = "color:#ff8a80;font-weight:bold;margin-bottom:6px;";
        panelEl.appendChild(div);
    }

    // "Select all · Deselect all" for a review panel: sets every id in or out.
    function selectLinks(panelEl, excludedObj, ids, onDone) {
        var div = document.createElement("div");
        div.style.cssText = "margin-bottom:4px;";
        [["Select all", false], ["Deselect all", true]].forEach(function (pair, i) {
            var link = document.createElement("span");
            link.textContent = pair[0];
            link.style.cssText = "color:#64b5f6;text-decoration:underline;cursor:pointer;" + (i ? "margin-left:14px;" : "");
            link.addEventListener("click", function () {
                ids.forEach(function (id) { excludedObj[id] = pair[1]; });
                onDone();
            });
            div.appendChild(link);
        });
        panelEl.appendChild(div);
    }

    // Set a field the way typing would, so the page's own code sees the change.
    function setField(input, value) {
        var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        setter.call(input, String(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function stepName(step) {
        var r = (SHOP.route || []).filter(function (x) { return x.step === step; })[0];
        return r ? r.name : step;
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

    // ---- Pricing one Add Listing row ----------------------------------------
    // Returns {price, qty, plan} to fill, or {hold: reason}. `rolls` are the
    // copy's damage/accuracy (weapons) or armor shown on the row; `isCopy`
    // means a weapon/armor row (one copy, ticked via "Select").
    function planListing(itemId, entry, rolls, isCopy, owned, shopHeldSoFar) {
        var base = BASE_STATS[itemId];
        var quality = null;
        if (rolls) {
            if (entry.fit) {
                quality = qualityFromFit(entry.fit, rolls);
            } else if (base) {
                quality = base.type === "Weapon"
                    ? ((rolls.damage || 0) - base.damage + ((rolls.accuracy || 0) - base.accuracy)) * 10
                    : ((rolls.armor || 0) - base.armor) * 20;
            }
        }
        var plan = entry.plan || null;
        var listPrice = entry.list;
        var shopMin = entry.shopMin;
        var refMin = entry.refMin;
        if (plan === "shop") {
            var routed = SHOP.items[String(itemId)];
            // The shop run takes specific weapon/armor copies (by uid), which this
            // page can't see: hold only as many copies as were routed there.
            var routedCopies = routed && routed.uids ? routed.uids.length : null;
            if (routed && (!isCopy || routedCopies == null || (shopHeldSoFar || 0) < routedCopies)) {
                return { hold: "sell at " + stepName(routed.step) + " instead (shop run pays more)", shopHold: true };
            }
            if (!entry.fallback) {
                return { hold: "sell to a shop" };
            }
            // No shop takes it: list it with its market plan, without the shop floor.
            plan = entry.fallback.plan;
            listPrice = entry.fallback.list;
            shopMin = null;
            refMin = entry.fallback.refMin;
        }
        if (isCopy && (entry.keepAll || (entry.keepMinQuality != null && (quality == null || quality >= entry.keepMinQuality)))) {
            return { hold: "keeping your best copy" };
        }
        if (!plan || plan === "keep") {
            return { hold: "Keep" };
        }
        if (entry.auto !== false && quality != null && quality >= CFG.topMinQuality) {
            plan = "top";
        }
        var basis = PLAN_BASIS[plan] || CFG.priceBasis;
        var price = null;
        if (isCopy && entry.curve && quality != null) {
            price = interpolate(entry.curve, quality, basis);
            if (price != null && plan === "dump") {
                price = Math.max(Math.round(price * (1 - CFG.dumpUndercutPct / 100)), 1);
            }
        }
        if (isCopy && price != null && entry.fast > 0) {
            // A thin quality band can rest on one cheap listing; no copy is worth
            // less than the item's cheapest listing of any quality.
            var floor = plan === "dump" ? entry.fast * (1 - CFG.dumpUndercutPct / 100) : entry.fast;
            price = Math.max(price, Math.round(floor));
        }
        if (price == null || !(price > 0)) {
            price = listPrice != null ? listPrice : (entry[basis] || null);
        }
        if (price == null || !(price > 0)) {
            return { hold: "no price" };
        }
        if (shopMin) {
            price = Math.max(price, shopMin);
        }
        price = Math.round(price);
        if (refMin && price < refMin) {
            return { hold: fmtMoney(price) + " is under half Torn's reference price, check the market" };
        }
        var qty = isCopy ? 1 : Math.min(Number(entry.qty) || 0, owned || 0);
        if (qty <= 0) {
            return { hold: "nothing to sell after what you keep" };
        }
        return { price: price, qty: qty, plan: plan, quality: quality };
    }

    // ---- Add Listing: one fixed button, one action per click ---------------
    // Click 1 fills every planned row (switches to "View All" and scrolls the
    // list, since Torn only renders rows near the screen). Click 2 presses
    // Torn's ADD TO MARKET; click 3 confirms if Torn asks. Nothing is listed
    // until you click, and the "anonymous (+10% fee)" box is never touched.
    function listMode() {
        var excluded = {};
        var filled = {};
        var held = {};
        var shopHeld = {};         // weapon/armor copies held back for the shop run, per item
        var shopKeys = {};         // ...and which rows (by rolls) they are, so a redraw isn't recounted
        var phase = "plan";        // plan -> filling -> filled -> adding -> done
        var loading = false;       // filling: getting Torn to load the whole tab
        var currentTab = "";
        var busyUntil = 0;
        var panelSig = "";
        var progress = 0;

        var btn = document.createElement("button");
        btn.id = "th-list-btn";
        btn.type = "button";
        btn.style.cssText =
            "position:fixed;left:50%;top:96px;transform:translateX(-50%);z-index:999999;" +
            "min-width:360px;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);display:none;";
        document.body.appendChild(btn);

        var panel = document.createElement("div");
        panel.id = "th-list-review";
        panel.style.cssText =
            "position:fixed;left:50%;top:160px;transform:translateX(-50%);z-index:999999;" +
            "min-width:360px;max-width:560px;max-height:42vh;overflow:auto;padding:8px 12px;border-radius:8px;" +
            "background:rgba(0,0,0,.92);border:1px solid #2e7d32;color:#eee;font:13px/1.5 Arial,sans-serif;display:none;";
        document.body.appendChild(panel);

        function onAddListing() {
            return /addListing/i.test(location.hash);
        }

        // What the app planned, before touching the page: one line per item.
        function plannedLines() {
            var lines = [];
            Object.keys(PRICE_MAP).forEach(function (id) {
                var e = PRICE_MAP[id];
                var plan = e.plan;
                var list = e.list;
                var copies = e.copies;
                if (plan === "shop") {
                    var routed = SHOP.items[id];
                    if (!e.fallback || (routed && (copies == null || !routed.uids))) {
                        return;
                    }
                    if (routed && copies != null) {
                        copies = Math.max(copies - routed.uids.length, 0);
                    }
                    plan = e.fallback.plan;
                    list = e.fallback.list;
                }
                if (!plan || plan === "keep") {
                    return;
                }
                if (copies != null) {
                    if (copies > 0) {
                        var value = e.copies ? (e.estimate || 0) * copies / e.copies : 0;
                        lines.push({ id: id, name: e.name, qty: copies, value: value, copies: true });
                    }
                } else if ((e.qty || 0) > 0 && list) {
                    lines.push({ id: id, name: e.name, qty: e.qty, price: list, value: e.qty * list });
                }
            });
            lines.sort(function (a, b) { return b.value - a.value; });
            return lines;
        }

        function rowInfo(row) {
            var img = row.querySelector("img");
            var m = img && /items[/]([0-9]+)[/]/.exec(img.getAttribute("src") || "");
            if (!m) {
                return null;
            }
            var name = img.alt || "";
            var select = row.querySelector("input[type=checkbox][aria-label^='Select ']");
            var qtyBox = row.querySelector("input[aria-label$=' amount']");
            var priceBox = row.querySelector("input[aria-label$=' price']");
            var text = (row.innerText || "").split(NL).join(" ");
            var rest = text.slice(text.indexOf(name) + name.length);
            var dollar = rest.indexOf("$");
            if (dollar >= 0) {
                rest = rest.slice(0, dollar);
            }
            var nums = (rest.match(/[0-9]+[.][0-9]+/g) || []).map(Number);
            var rolls = null;
            if (select && nums.length >= 2) {
                rolls = { damage: nums[0], accuracy: nums[1], armor: null };
            } else if (select && nums.length === 1) {
                rolls = { damage: null, accuracy: null, armor: nums[0] };
            }
            return {
                id: m[1], name: name, select: select, qtyBox: qtyBox, priceBox: priceBox, rolls: rolls,
                owned: qtyBox ? parseInt(qtyBox.getAttribute("data-money"), 10) || 0 : 1,
                key: m[1] + "|" + nums.join("|")
            };
        }

        // "View All" folds several copies of a weapon/armor into one row with an
        // "Expand N <name> items" button; open the ones we plan to sell from.
        function expandPlannedGroups() {
            var planned = {};
            plannedLines().forEach(function (l) {
                if (!excluded[l.id]) {
                    planned[PRICE_MAP[l.id].name] = true;
                }
            });
            var opened = 0;
            var buttons = document.querySelectorAll("button[aria-label^='Expand '][aria-expanded='false']");
            for (var i = 0; i < buttons.length; i++) {
                var m = /^Expand [0-9,]+ (.+) items?$/.exec(buttons[i].getAttribute("aria-label") || "");
                if (m && planned[m[1]]) {
                    buttons[i].click();
                    opened += 1;
                }
            }
            return opened;
        }

        function scanVisible() {
            expandPlannedGroups();
            var rows = document.querySelectorAll("[class*='itemRow___']");
            for (var i = 0; i < rows.length; i++) {
                var info = rowInfo(rows[i]);
                if (!info || !info.priceBox) {
                    continue;
                }
                var entry = PRICE_MAP[info.id];
                if (!entry || excluded[info.id] || held[info.key] || shopKeys[info.key]) {
                    continue;
                }
                // A row that already has a price was filled (by us or by you).
                // Copies can share identical rolls, so the row's own state, not
                // its rolls, decides whether it still needs filling.
                if (info.priceBox.value) {
                    continue;
                }
                var d = planListing(info.id, entry, info.rolls, !!info.select, info.owned, shopHeld[info.id]);
                if (d.hold) {
                    if (d.shopHold && info.select) {
                        // One copy per row: count it once, and remember the row so the
                        // list redrawing it later doesn't count (or list) it again.
                        shopHeld[info.id] = (shopHeld[info.id] || 0) + 1;
                        shopKeys[info.key] = true;
                        held[info.key + "#shop"] = { name: info.name, reason: d.hold };
                        continue;
                    }
                    held[info.key] = { name: info.name, reason: d.hold };
                    continue;
                }
                if (info.select) {
                    if (!info.select.checked) {
                        info.select.click();
                    }
                } else if (info.qtyBox) {
                    setField(info.qtyBox, d.qty);
                } else {
                    continue;
                }
                setField(info.priceBox, d.price);
                filled[info.key + "#" + Object.keys(filled).length] = { id: info.id, name: info.name, qty: d.qty, price: d.price };
            }
        }

        function lastRowName() {
            var imgs = document.querySelectorAll("[class*='itemRow___'] img, [class*='virtualListing___'] img");
            return imgs.length ? imgs[imgs.length - 1].alt : "";
        }

        // Tabs holding something planned for sale. Category tabs list each
        // weapon/armor copy on its own row; anything without a known tab is
        // picked up by a final "View All" pass.
        function plannedTabs() {
            var tabs = [];
            var needAll = false;
            plannedLines().forEach(function (l) {
                if (excluded[l.id]) {
                    return;
                }
                var t = PRICE_MAP[l.id].tab;
                if (!t || !document.querySelector("button[aria-label='View " + t + "']")) {
                    needAll = true;
                } else if (tabs.indexOf(t) === -1) {
                    tabs.push(t);
                }
            });
            if (needAll) {
                tabs.push("All");
            }
            return tabs;
        }

        // For each planned tab: open it, let Torn load the whole list (it only
        // shows ~80 rows and fetches more at the bottom), then scroll top to
        // bottom filling rows; stop at the bottom once the list stops growing.
        function fillAll() {
            phase = "filling";
            var tabs = plannedTabs();
            function nextTab() {
                if (!tabs.length) {
                    window.scrollTo(0, 0);
                    currentTab = "";
                    phase = "filled";
                    render();
                    return;
                }
                currentTab = tabs.shift();
                var tabBtn = document.querySelector("button[aria-label='View " + currentTab + "']");
                if (!tabBtn) {
                    nextTab();
                    return;
                }
                if (tabBtn.getAttribute("aria-selected") !== "true") {
                    tabBtn.click();
                }
                window.scrollTo(0, 0);
                setTimeout(function () {
                    loading = true;
                    loadAll(fillPass);
                }, 1200);
            }
            function loadAll(done) {
                var stalls = 0;
                var lastSig = "";
                function nudge() {
                    var h = document.documentElement.scrollHeight;
                    if (h <= window.innerHeight + 50) {
                        loading = false;
                        done();
                        return;
                    }
                    window.scrollTo(0, Math.max(h - window.innerHeight - 300, 0));
                    setTimeout(function () {
                        window.scrollTo(0, document.documentElement.scrollHeight);
                        setTimeout(function () {
                            var sig = document.documentElement.scrollHeight + "|" + lastRowName();
                            stalls = sig === lastSig ? stalls + 1 : 0;
                            lastSig = sig;
                            render();
                            if (stalls >= 2) {
                                loading = false;
                                window.scrollTo(0, 0);
                                setTimeout(done, 500);
                            } else {
                                nudge();
                            }
                        }, 1000);
                    }, 200);
                }
                nudge();
            }
            function fillPass() {
                var bottomChecks = 0;
                function step() {
                    scanVisible();
                    progress = Object.keys(filled).length;
                    render();
                    var bottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 5;
                    if (bottom) {
                        bottomChecks += 1;
                        if (bottomChecks >= 2) {
                            nextTab();
                            return;
                        }
                        setTimeout(step, 500);
                        return;
                    }
                    bottomChecks = 0;
                    window.scrollBy(0, Math.floor(window.innerHeight * 0.7));
                    setTimeout(step, 350);
                }
                step();
            }
            nextTab();
        }

        // Torn's own running total: "You are adding N items across M categories for a total of $X"
        function tornTotals() {
            var els = document.querySelectorAll("div, p, span");
            for (var i = els.length - 1; i >= 0; i--) {
                var t = (els[i].innerText || "").trim();
                var m = /^You are adding ([0-9,]+) items? across [0-9,]+ categor(?:y|ies) for a total of [$]([0-9,]+)/.exec(t);
                if (m) {
                    return { items: parseInt(m[1].replace(/,/g, ""), 10), total: parseInt(m[2].replace(/,/g, ""), 10) };
                }
            }
            return null;
        }

        function addButton() {
            var all = document.querySelectorAll("button");
            for (var i = 0; i < all.length; i++) {
                if (/^ *ADD TO MARKET *$/i.test(all[i].textContent || "") && all[i] !== btn) {
                    return all[i];
                }
            }
            return null;
        }

        // A confirmation Torn may show after ADD TO MARKET.
        function confirmButton() {
            var add = addButton();
            var all = document.querySelectorAll("button, a, input[type=button], input[type=submit]");
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if (el === btn || el === add || el.offsetParent === null || panel.contains(el)) {
                    continue;
                }
                var text = (el.textContent || el.value || "").trim();
                if (!/^(yes|confirm|add|list|add to market|list items)$/i.test(text)) {
                    continue;
                }
                var box = el;
                for (var up = 0; up < 5 && box && !/are you sure|confirm/i.test(box.textContent || ""); up++) {
                    box = box.parentElement;
                }
                if (box && /are you sure|confirm/i.test(box.textContent || "")) {
                    return el;
                }
            }
            return null;
        }

        function line(text, style, onClick) {
            var div = document.createElement("div");
            div.textContent = text;
            div.style.cssText = style || "";
            if (onClick) {
                div.style.cursor = "pointer";
                div.addEventListener("click", onClick);
            }
            panel.appendChild(div);
        }

        function renderPanel() {
            var sig;
            if (phase === "plan") {
                var lines = plannedLines();
                sig = "plan:" + document.querySelectorAll("#th-list-btn").length + ":" + ((tornTotals() || {}).items || 0) + ":" + lines.map(function (l) { return l.id + (excluded[l.id] ? "x" : ""); }).join(",");
                if (sig === panelSig) {
                    return;
                }
                panelSig = sig;
                // Rebuilding the panel resets its scroll; keep the reader's place.
                var keepTop = panel.scrollTop;
                panel.textContent = "";
                var total = 0;
                var picked = 0;
                lines.forEach(function (l) {
                    if (!excluded[l.id]) {
                        total += l.value;
                        picked += 1;
                    }
                });
                copiesWarning(panel, "th-list-btn");
                var already = tornTotals();
                if (already && already.items > 0) {
                    line("Torn already has " + already.items.toLocaleString("en-US") + " item(s) selected from before. " +
                        "Clear them first with Torn's Clear all, so you only add what you planned.", "color:#ffb74d;margin-bottom:6px;");
                }
                line("Planned listings (" + picked + " of " + lines.length + " items, about " + fmtMoney(total) + "). Click a line to leave it out:",
                    "color:#9ccc65;margin-bottom:4px;");
                selectLinks(panel, excluded, lines.map(function (l) { return l.id; }), render);
                lines.forEach(function (l) {
                    var x = excluded[l.id];
                    var text = (x ? "✕ " : "✓ ") + l.qty.toLocaleString("en-US") + " × " + l.name +
                        (l.copies ? " (priced per copy, about " + fmtMoney(l.value) + ")" : " @ " + fmtMoney(l.price) + " = " + fmtMoney(l.value));
                    line(text, x ? "text-decoration:line-through;color:#888;" : "", function () {
                        excluded[l.id] = !excluded[l.id];
                        render();
                    });
                });
                panel.scrollTop = keepTop;
                return;
            }
            var keys = Object.keys(filled);
            var hkeys = Object.keys(held);
            var tt = tornTotals();
            sig = phase + ":" + keys.length + ":" + hkeys.length + ":" + (tt ? tt.items : "");
            if (sig === panelSig) {
                return;
            }
            panelSig = sig;
            var keepTop = panel.scrollTop;
            panel.textContent = "";
            var byName = {};
            var order = [];
            keys.forEach(function (k) {
                var f = filled[k];
                if (!byName[f.name]) {
                    byName[f.name] = { qty: 0, value: 0, min: f.price, max: f.price };
                    order.push(f.name);
                }
                var g = byName[f.name];
                g.qty += f.qty;
                g.value += f.qty * f.price;
                g.min = Math.min(g.min, f.price);
                g.max = Math.max(g.max, f.price);
            });
            order.sort(function (a, b) { return byName[b].value - byName[a].value; });
            line((phase === "filling" ? "Filling… " : "Filled ") + keys.length + " row(s):", "color:#9ccc65;margin-bottom:4px;");
            order.forEach(function (n) {
                var g = byName[n];
                var each = g.min === g.max ? fmtMoney(g.min) : fmtMoney(g.min) + " to " + fmtMoney(g.max);
                line("  " + g.qty.toLocaleString("en-US") + " × " + n + " @ " + each + " = " + fmtMoney(g.value));
            });
            if (hkeys.length) {
                line("Held back " + hkeys.length + " row(s):", "color:#ffb74d;margin-top:6px;");
                hkeys.forEach(function (k) {
                    line("  " + held[k].name + ": " + held[k].reason, "color:#bbb;");
                });
            }
            if (phase === "filled") {
                var ours = keys.reduce(function (n, k) { return n + filled[k].qty; }, 0);
                if (tt && tt.items !== ours) {
                    line("Check before adding: Torn has " + tt.items.toLocaleString("en-US") + " item(s) selected but TornHoarder filled " +
                        ours.toLocaleString("en-US") + ". The difference is selections from before or ones you made.",
                        "color:#ffb74d;margin-top:6px;");
                }
                line("Start over (clears Torn's selections)", "color:#64b5f6;margin-top:6px;text-decoration:underline;", startOver);
            }
            panel.scrollTop = keepTop;
        }

        function startOver() {
            var clear = Array.prototype.filter.call(document.querySelectorAll("a, button, span"), function (e) {
                return /^Clear all$/i.test((e.textContent || "").trim());
            })[0];
            if (clear) {
                clear.click();
            }
            filled = {};
            held = {};
            shopHeld = {};
            shopKeys = {};
            phase = "plan";
            panelSig = "";
            render();
        }

        function render() {
            var show = onAddListing();
            btn.style.display = show ? "block" : "none";
            panel.style.display = show ? "block" : "none";
            if (!show) {
                return;
            }
            renderPanel();
            var totals = tornTotals();
            var c = confirmButton();
            if (phase === "filling") {
                btn.textContent = (loading ? "Loading " : "Filling ") + currentTab + "… " + progress + " row(s) filled";
            } else if (Date.now() < busyUntil) {
                btn.textContent = "Adding…";
            } else if (c) {
                btn.textContent = "Confirm: add " + (totals ? totals.items : "") + " listing(s)";
            } else if (phase === "plan") {
                var all = plannedLines();
                var lines = all.filter(function (l) { return !excluded[l.id]; });
                btn.textContent = lines.length ? "Fill " + lines.length + " item(s) for listing"
                    : all.length ? "Nothing selected: pick items in the list" : "Nothing planned to list";
            } else if (totals && totals.items > 0) {
                btn.textContent = "Add " + totals.items.toLocaleString("en-US") + " listing(s) to market · " + fmtMoney(totals.total);
            } else if (phase === "adding" || phase === "done") {
                btn.textContent = "Listed ✓";
                phase = "done";
            } else {
                btn.textContent = "Nothing filled. Start over below";
            }
        }

        btn.addEventListener("click", function () {
            if (phase === "filling" || Date.now() < busyUntil) {
                return;
            }
            var c = confirmButton();
            if (c) {
                busyUntil = Date.now() + 2500;
                c.click();
                render();
                return;
            }
            if (phase === "plan") {
                if (plannedLines().some(function (l) { return !excluded[l.id]; })) {
                    fillAll();
                }
                return;
            }
            var totals = tornTotals();
            var add = addButton();
            if (phase === "filled" && totals && totals.items > 0 && add && !add.disabled) {
                phase = "adding";
                busyUntil = Date.now() + 2500;
                add.click();
                render();
            }
        });

        window.__TH_LIST__ = { planListing: planListing, plannedLines: plannedLines, rowInfo: rowInfo, scanVisible: scanVisible, expandPlannedGroups: expandPlannedGroups, selectLinks: selectLinks };
        render();
        setInterval(render, 700);
        console.log("[TornHoarder] list mode v" + VER + ": " + plannedLines().length + " item(s) planned");
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
        var ADD_LISTING_URL = "https://www.torn.com/page.php?sid=ItemMarket#/addListing";
        var shopName = (route.filter(function (r) { return r.step === step; })[0] || {}).name || step;

        // What this run sold, kept in the browser across shop pages. A sale is
        // recorded when Confirm is clicked here; one run = one built script.
        var RUN_KEY = "tornhoarder.shopRun";
        function loadRun() {
            try {
                var r = JSON.parse(localStorage.getItem(RUN_KEY) || "null");
                if (r && r.built === SHOP.built) {
                    return r;
                }
            } catch (e) { /* storage blocked or corrupt: start fresh */ }
            return { built: SHOP.built, sold: [] };
        }
        function saveRun(r) {
            try {
                localStorage.setItem(RUN_KEY, JSON.stringify(r));
            } catch (e) { /* storage blocked: the summary just won't persist */ }
        }
        function soldTotal(run) {
            return run.sold.reduce(function (sum, x) { return sum + x.qty * x.price; }, 0);
        }
        var pending = null;

        var btn = document.createElement("button");
        btn.id = "tma-shop-btn";
        btn.type = "button";
        btn.style.cssText =
            "position:fixed;left:50%;top:96px;transform:translateX(-50%);z-index:999999;" +
            "min-width:340px;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);";
        document.body.appendChild(btn);

        // Review list under the button: what click 1 will fill, line by line.
        // Clicking a line leaves that item out of this shop's sale (and back in).
        var excluded = {};
        var panelSig = "";
        var panel = document.createElement("div");
        panel.id = "tma-shop-review";
        panel.style.cssText =
            "position:fixed;left:50%;top:160px;transform:translateX(-50%);z-index:999999;" +
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
            var sig = document.querySelectorAll("#tma-shop-btn").length + "#" +
                order.map(function (id) { return id + ":" + byId[id].qty + ":" + !!excluded[id]; }).join("|");
            panel.style.display = "block";
            if (sig === panelSig) {
                return;
            }
            panelSig = sig;
            var keepTop = panel.scrollTop;
            panel.textContent = "";
            var head = document.createElement("div");
            head.textContent = "Will sell here (click a line to leave it out):";
            head.style.cssText = "color:#9ccc65;margin-bottom:4px;";
            panel.appendChild(head);
            copiesWarning(panel, "tma-shop-btn");
            selectLinks(panel, excluded, order, render);
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
            panel.scrollTop = keepTop;
        }

        function renderSummary(run) {
            var sig = "summary:" + run.sold.length;
            panel.style.display = "block";
            if (sig === panelSig) {
                return;
            }
            panelSig = sig;
            panel.textContent = "";
            var head = document.createElement("div");
            head.style.cssText = "color:#9ccc65;margin-bottom:4px;";
            panel.appendChild(head);
            if (!run.sold.length) {
                head.textContent = "Shop run finished. No sales were confirmed with this button, so there is nothing to total.";
                return;
            }
            var byShop = {};
            var shops = [];
            run.sold.forEach(function (x) {
                if (!byShop[x.shop]) {
                    byShop[x.shop] = { qty: 0, value: 0, items: {} };
                    shops.push(x.shop);
                }
                var g = byShop[x.shop];
                g.qty += x.qty;
                g.value += x.qty * x.price;
                g.items[x.name] = (g.items[x.name] || 0) + x.qty;
            });
            var units = run.sold.reduce(function (n, x) { return n + x.qty; }, 0);
            head.textContent = "Shop run sold " + units.toLocaleString("en-US") + " item(s) for " + money(soldTotal(run)) + ":";
            shops.forEach(function (name) {
                var g = byShop[name];
                var line = document.createElement("div");
                line.style.cssText = "margin-top:4px;";
                line.textContent = name + ": " + g.qty.toLocaleString("en-US") + " item(s) · " + money(g.value);
                panel.appendChild(line);
                Object.keys(g.items).forEach(function (item) {
                    var sub = document.createElement("div");
                    sub.style.cssText = "color:#bbb;padding-left:12px;";
                    sub.textContent = g.items[item].toLocaleString("en-US") + " × " + item;
                    panel.appendChild(sub);
                });
            });
            var tip = document.createElement("div");
            tip.style.cssText = "color:#bbb;margin-top:6px;";
            tip.textContent = "Next: list everything else on the Item Market (the button above). Run the app again first if you want its plan to drop what you just sold.";
            panel.appendChild(tip);
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
                var run = loadRun();
                var sold = soldTotal(run);
                if (nxt) {
                    btn.textContent = "Next: " + nxt.name + " (" + nxt.items.toLocaleString("en-US") + ") →" +
                        (sold ? " · " + money(sold) + " sold so far" : "");
                } else {
                    btn.textContent = "Shop run done ✓ " + (sold ? money(sold) + " sold · " : "") + "Next: list the rest →";
                    renderSummary(run);
                }
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
                if (pending) {
                    var run = loadRun();
                    run.sold = run.sold.concat(pending);
                    saveRun(run);
                    pending = null;
                }
                c.click();
                render();
                return;
            }
            var rows = ourRows();
            if (rows.length) {
                rows.forEach(setAmount);
                pending = rows.map(function (r) { return { shop: shopName, name: r.name, qty: r.qty, price: r.price }; });
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
            location.href = nxt ? nxt.url : ADD_LISTING_URL;
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

    if (location.pathname.indexOf("/page.php") === 0 && /sid=ItemMarket/i.test(location.search)) {
        listMode();
    }
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
        # "built" tells one run's sales record apart from the next one's.
        .replace("__SHOP__", json.dumps(dict(shop_run or {"route": [], "items": {}}, built=int(time.time()))))
        .replace("__VERSION__", __version__)
    )