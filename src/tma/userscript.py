from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Dict, Optional

from . import __version__

TEMPLATE = """\
// ==UserScript==
// @name         TornHoarder
// @namespace    tornhoarder
// @version      __VERSION__.__BUILD__
// @description  TornHoarder: fills Add Listing prices from your sell plan and runs one-button city shop sales.
// @match        https://www.torn.com/*
// @updateURL    http://127.0.0.1:9999/tornhoarder.user.js
// @downloadURL  http://127.0.0.1:9999/tornhoarder.user.js
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==
// TornHoarder template __TEMPLATE__

(function () {
    "use strict";

    var PRICE_MAP = __PRICE_MAP__;
    var BASE_STATS = __BASE_STATS__;
    var CFG = __CFG__;
    var SHOP = __SHOP__;
    var STOCK = __STOCK__;

    Object.keys(PRICE_MAP).forEach(function (itemId) {
        PRICE_MAP[itemId].fast = Number(PRICE_MAP[itemId].fast);
        PRICE_MAP[itemId].fair = Number(PRICE_MAP[itemId].fair);
        PRICE_MAP[itemId].greedy = Number(PRICE_MAP[itemId].greedy);
    });

    var VER = (typeof GM_info !== "undefined" && GM_info.script)
        ? GM_info.script.version
        : "__VERSION__";
    // Diagnostics go to the console and to the app (data/tornhoarder.log.jsonl),
    // so a failure abroad can be read afterwards without the console.
    function thLog(msg) {
        console.log(msg);
        if (typeof GM_xmlhttpRequest !== "undefined") {
            GM_xmlhttpRequest({
                method: "POST", url: CFG.reportUrl.replace(/shop-report$/, "script-log"),
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify({ ver: VER, page: location.pathname + location.search, msg: msg })
            });
        }
    }

    // Tells the app which build is running, so its sidebar can say whether an update took.
    if (window.top === window.self && typeof GM_xmlhttpRequest !== "undefined") {
        GM_xmlhttpRequest({ method: "GET", url: CFG.reportUrl.replace(/shop-report$/, "hello") + "?v=" + encodeURIComponent(VER) });
    }
    var PLAN_BASIS = { dump: "fast", competitive: "fast", top: "greedy" };
    var NL = String.fromCharCode(10);

    function fmtMoney(n) {
        return "$" + Math.round(n).toLocaleString("en-US");
    }

    // Which extension runs this copy, e.g. "Tampermonkey 5.3.3" (BETA builds
    // have their own version numbers), so duplicates can be found and removed.
    var HANDLER = (typeof GM_info !== "undefined")
        ? ((GM_info.scriptHandler || "userscript manager") + (GM_info.version ? " " + GM_info.version : ""))
        : "unknown manager";

    function labelCopy(button) {
        button.setAttribute("data-th-copy", "v" + VER + " in " + HANDLER);
    }

    // Several installed copies each draw their own button in the same spot, so
    // the one on top may be stale. Warn from inside the panel, naming each copy.
    function copiesWarning(panelEl, buttonId) {
        var buttons = document.querySelectorAll("#" + buttonId);
        if (buttons.length < 2) {
            return;
        }
        var copies = Array.prototype.map.call(buttons, function (b) {
            return b.getAttribute("data-th-copy") || "an old copy (before v1.3.8)";
        });
        var div = document.createElement("div");
        div.textContent = buttons.length + " copies of TornHoarder are running on this page: " + copies.join("; ") + ". " +
            "Each userscript extension (Tampermonkey, Tampermonkey BETA, Violentmonkey…) runs its own copy. " +
            "Keep TornHoarder in one extension only and delete it from the others.";
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

    // Torn marks <body data-traveling="true"> in the air and data-abroad once
    // landed elsewhere; shops and the Item Market are closed either way.
    function awayMessage() {
        var d = (document.body && document.body.dataset) || {};
        if (d.traveling === "true") {
            return "✈ You're traveling: TornHoarder is paused until you land in Torn";
        }
        if (d.abroad === "true") {
            return "✈ You're abroad: TornHoarder is paused until you're back in Torn";
        }
        return "";
    }

    function readPref(key, fallback) {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (e) {
            return fallback;
        }
    }

    function writePref(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (e) { /* storage blocked: the choice lasts until reload */ }
    }

    // Holds a mode's button and review panel. Docks centered over the page, or
    // left/right in the empty margins; the list folds away; close hides it all
    // until the page reloads. Side and fold are remembered in this browser.
    var DOCK_KEY = "tornhoarder.dock";
    function makeDock(btn, panel) {
        var side = readPref(DOCK_KEY, (window.innerWidth || 0) >= 1500 ? "right" : "center");
        var folded = readPref(DOCK_KEY + ".folded", "") === "1";
        var closed = false;
        var shown = true;
        var host = document.createElement("div");
        host.id = "th-dock";
        var bar = document.createElement("div");
        bar.style.cssText = "display:flex;justify-content:flex-end;gap:4px;";
        var wrap = document.createElement("div");
        var controls = {};
        [["left", "◀", "Dock in the left margin"], ["center", "▣", "Center over the page"],
         ["right", "▶", "Dock in the right margin"], ["fold", "–", "Hide or show the list"],
         ["close", "✕", "Close until the page reloads"]].forEach(function (c) {
            var b = document.createElement("button");
            b.type = "button";
            b.textContent = c[1];
            b.title = c[2];
            b.addEventListener("click", function () {
                if (c[0] === "close") {
                    closed = true;
                } else if (c[0] === "fold") {
                    folded = !folded;
                    writePref(DOCK_KEY + ".folded", folded ? "1" : "");
                } else {
                    side = c[0];
                    writePref(DOCK_KEY, side);
                }
                place();
            });
            controls[c[0]] = b;
            bar.appendChild(b);
        });
        host.appendChild(bar);
        host.appendChild(btn);
        wrap.appendChild(panel);
        host.appendChild(wrap);
        document.body.appendChild(host);

        function place() {
            var docked = side !== "center";
            host.style.cssText = "position:fixed;top:72px;z-index:999999;flex-direction:column;gap:6px;" +
                "display:" + (shown && !closed ? "flex" : "none") + ";" +
                (side === "left" ? "left:12px;width:340px;" : side === "right" ? "right:12px;width:340px;"
                    : "left:50%;transform:translateX(-50%);");
            btn.style.minWidth = docked ? "0" : "360px";
            panel.style.maxHeight = docked ? "calc(100vh - 190px)" : "42vh";
            wrap.style.display = folded ? "none" : "block";
            Object.keys(controls).forEach(function (k) {
                var on = k === side || (k === "fold" && folded);
                controls[k].style.cssText = "padding:1px 8px;border-radius:4px;border:1px solid #2e7d32;cursor:pointer;" +
                    "font:12px/1.4 Arial,sans-serif;color:#fff;background:" + (on ? "#2e7d32" : "rgba(0,0,0,.75)") + ";";
            });
        }
        place();
        return {
            close: function () {
                closed = true;
                place();
            },
            // Off-page (e.g. another Item Market tab): hide without closing.
            show: function (on) {
                if (shown !== on) {
                    shown = on;
                    place();
                }
            }
        };
    }

    // ---- After selling: bank the cash, optionally hide in hospital ----------
    var DONATE_URL = "https://www.torn.com/factions.php?step=your#/tab=armoury&start=0&sub=donate";
    var MEDICAL_URL = "https://www.torn.com/item.php#medical-items";
    var IPECAC_ID = "1363";
    var IPECAC_KEY = "tornhoarder.ipecac";   // set when you choose Ipecac, read on the items page
    var IPECAC_FRESH_MS = 15 * 60 * 1000;

    // The wallet as Torn's sidebar shows it right now.
    function walletNow() {
        var el = document.querySelector("#user-money");
        var n = el ? parseInt(el.getAttribute("data-money"), 10) : NaN;
        return isNaN(n) ? null : n;
    }

    // Everything above the cash you keep on hand; 0 when there's nothing to bank.
    function depositAmount(wallet) {
        return wallet == null ? 0 : Math.max(wallet - (Number(CFG.keepCash) || 0), 0);
    }

    // Deposit and Ipecac offers for the end of a shop run or listing.
    // `value` is what was just sold or listed.
    function afterLinks(panelEl, value) {
        var amount = depositAmount(walletNow());
        var link = function (text, onClick) {
            var div = document.createElement("div");
            div.textContent = text;
            div.style.cssText = "color:#64b5f6;text-decoration:underline;cursor:pointer;margin-top:6px;";
            div.addEventListener("click", onClick);
            panelEl.appendChild(div);
        };
        if (amount > 0) {
            link("Deposit " + fmtMoney(amount) + " to the faction vault (keeping " + fmtMoney(Number(CFG.keepCash) || 0) + ") →", function () {
                location.href = DONATE_URL;
            });
        }
        if (Number(CFG.ipecacOver) > 0 && value >= Number(CFG.ipecacOver)) {
            link("Take Ipecac Syrup: hospital for 60–90 min so nobody can mug you while it sells →", function () {
                writePref(IPECAC_KEY, String(Date.now()));
                location.href = MEDICAL_URL;
            });
        }
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
        if (plan === "trade") {
            // Never listed: a trader pays more than it would net here.
            return { hold: "trade to " + (entry.trader || "your trader") + " instead (pays more)" };
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
        var listedValue = 0;       // Torn's total for the batch you added, for the Ipecac offer

        var btn = document.createElement("button");
        btn.id = "th-list-btn";
        btn.type = "button";
        labelCopy(btn);
        btn.style.cssText =
            "width:100%;box-sizing:border-box;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);display:none;";

        var panel = document.createElement("div");
        panel.id = "th-list-review";
        panel.style.cssText =
            "box-sizing:border-box;max-width:560px;overflow:auto;padding:8px 12px;border-radius:8px;" +
            "background:rgba(0,0,0,.92);border:1px solid #2e7d32;color:#eee;font:13px/1.5 Arial,sans-serif;display:none;";
        var dock = makeDock(btn, panel);

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
                if (!plan || plan === "keep" || plan === "trade") {
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
            if (phase === "done") {
                line("Listed " + fmtMoney(listedValue) + ".", "color:#9ccc65;margin-top:6px;");
                afterLinks(panel, listedValue);
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
            dock.show(show);
            btn.style.display = show ? "block" : "none";
            panel.style.display = show ? "block" : "none";
            if (!show) {
                return;
            }
            if (awayMessage()) {
                btn.textContent = awayMessage();
                panel.style.display = "none";
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
                btn.textContent = "Listed ✓ (click to close)";
                phase = "done";
            } else {
                btn.textContent = "Nothing filled. Start over below";
            }
        }

        btn.addEventListener("click", function () {
            if (phase === "filling" || Date.now() < busyUntil || awayMessage()) {
                return;
            }
            if (phase === "done") {
                dock.close();
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
                listedValue = totals.total;
                busyUntil = Date.now() + 2500;
                add.click();
                render();
            }
        });

        window.__TH_LIST__ = { planListing: planListing, plannedLines: plannedLines, rowInfo: rowInfo, scanVisible: scanVisible, expandPlannedGroups: expandPlannedGroups, selectLinks: selectLinks, copiesWarning: copiesWarning };
        render();
        setInterval(render, 700);
        thLog("[TornHoarder] list mode v" + VER + ": " + plannedLines().length + " item(s) planned");
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
        labelCopy(btn);
        btn.style.cssText =
            "width:100%;box-sizing:border-box;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);";

        // Review list under the button: what click 1 will fill, line by line.
        // Clicking a line leaves that item out of this shop's sale (and back in).
        var excluded = {};
        var panelSig = "";
        var panel = document.createElement("div");
        panel.id = "tma-shop-review";
        panel.style.cssText =
            "box-sizing:border-box;overflow:auto;padding:8px 12px;border-radius:8px;" +
            "background:rgba(0,0,0,.9);border:1px solid #2e7d32;color:#eee;font:13px/1.5 Arial,sans-serif;";
        var dock = makeDock(btn, panel);

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
                afterLinks(panel, 0);
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
            afterLinks(panel, soldTotal(run));
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
            if (awayMessage()) {
                btn.textContent = awayMessage();
                panel.style.display = "none";
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
            if (Date.now() < busyUntil || awayMessage()) {
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
            if (!nxt) {
                dock.close();
            }
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
            thLog("[TornHoarder] reported " + step + ": buys " + Object.keys(buys).length + ", refused " + rejects.length);
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
        thLog("[TornHoarder] shop run v" + VER + ": " + route.length + " shop(s) on route");
    }

    // ---- Shared by the deposit, Ipecac and buy modes -----------------------
    function greenButton(id) {
        var b = document.createElement("button");
        b.id = id;
        b.type = "button";
        labelCopy(b);
        b.style.cssText =
            "width:100%;box-sizing:border-box;padding:14px 22px;border-radius:8px;border:2px solid #2e7d32;" +
            "background:#43a047;color:#fff;font:bold 16px/1.2 Arial,sans-serif;cursor:pointer;" +
            "box-shadow:0 4px 14px rgba(0,0,0,.5);";
        return b;
    }

    function infoPanel() {
        var p = document.createElement("div");
        p.style.cssText = "box-sizing:border-box;overflow:auto;padding:8px 12px;border-radius:8px;" +
            "background:rgba(0,0,0,.9);border:1px solid #2e7d32;color:#eee;font:13px/1.5 Arial,sans-serif;";
        return p;
    }

    function setLines(panelEl, lines) {
        var sig = lines.join(NL);
        if (panelEl.getAttribute("data-sig") === sig) {
            return;
        }
        panelEl.setAttribute("data-sig", sig);
        panelEl.textContent = "";
        lines.forEach(function (t) {
            var div = document.createElement("div");
            div.textContent = t;
            panelEl.appendChild(div);
        });
    }

    // Torn's "Are you sure…? Yes / No" prompt, found by its wording.
    // Torn's questions: "Are you sure…?" (city shops) or "Buy 18 x Dahlia for
    // $5,400?" (the shop abroad, seen live).
    var QUESTION = /are you sure|\\bbuy\\b[^?]*\\bfor\\s*\\$[0-9,]+\\s*\\?/i;

    function findConfirm(scope) {
        var all = (scope || document).querySelectorAll("button, a, input[type=button], input[type=submit], [role=button]");
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (el.offsetParent === null || el.closest("#th-dock")) {
                continue;
            }
            if (!/^(yes|confirm)$/i.test((el.textContent || el.value || "").trim())) {
                continue;
            }
            var box = el;
            for (var up = 0; up < 6 && box && !QUESTION.test(box.textContent || ""); up++) {
                box = box.parentElement;
            }
            if (box && QUESTION.test(box.textContent || "")) {
                return el;
            }
        }
        return null;
    }

    // After Buy, Torn's newer pages swap the row's controls for an inline
    // "are you sure": the confirm, page-wide or inside that row (not the
    // original Buy).
    function rowConfirm(row, buy) {
        var c = findConfirm();
        if (c || !row) {
            return c;
        }
        var buttons = Array.prototype.filter.call(row.querySelectorAll("button"), function (b) {
            return b !== buy && b.offsetParent !== null;
        });
        var label = function (b) { return (b.textContent || "").trim(); };
        return buttons.filter(function (b) { return /^(yes|confirm)/i.test(label(b)); })[0] ||
            (/are you sure|confirm/i.test(row.textContent || "") ?
                buttons.filter(function (b) { return /^buy/i.test(label(b)); })[0] : null) || null;
    }

    function logConfirmMiss(what, row) {
        thLog("[TornHoarder] " + what + " confirm not recognized. Row: " + ((row && row.textContent) || "").trim() +
            " · buttons on the page: " + Array.prototype.map.call(document.querySelectorAll("button"), function (b) {
                return (b.textContent || "").trim();
            }).join(" | "));
    }

    // Torn's money boxes: a visible formatted field plus a hidden twin.
    function setMoney(visible, hidden, n) {
        visible.value = String(n);
        ["input", "keyup", "change"].forEach(function (ev) {
            visible.dispatchEvent(new Event(ev, { bubbles: true }));
        });
        if (hidden) {
            hidden.value = String(n);
        }
    }

    function usable(b) {
        return b && !b.disabled && !/\\bdisabled\\b/.test(b.className || "");
    }

    // ---- Faction vault: deposit everything above what you keep -------------
    // Click 1 fills DEPOSIT MONEY's amount (wallet minus keepCash, read live)
    // and presses it; click 2 confirms if Torn asks.
    function depositMode() {
        var btn = greenButton("th-deposit-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);
        var busyUntil = 0;
        var note = "";

        function form() {
            var boxes = document.querySelectorAll("div.donate");
            for (var i = 0; i < boxes.length; i++) {
                var go = Array.prototype.filter.call(boxes[i].querySelectorAll("button"), function (b) {
                    return /deposit money/i.test(b.textContent || "");
                })[0];
                var input = boxes[i].querySelector("input.input-money:not([type=hidden])");
                if (go && input && input.offsetParent !== null) {
                    return { go: go, input: input, hidden: boxes[i].querySelector("input.input-money[type=hidden]") };
                }
            }
            return null;
        }

        function render() {
            var f = form();
            var c = findConfirm();
            dock.show(!!(f || c));
            if (!f && !c) {
                return;
            }
            var wallet = walletNow();
            var amount = depositAmount(wallet);
            var keep = Number(CFG.keepCash) || 0;
            setLines(panel, [
                "Wallet " + (wallet == null ? "?" : fmtMoney(wallet)) + " · keeping " + fmtMoney(keep) +
                    " (set under Rules in the app) · deposit " + fmtMoney(amount),
                "Faction vault money can be taken back by your faction's leadership, not by muggers."
            ]);
            if (Date.now() < busyUntil) {
                btn.textContent = "Depositing…";
            } else if (c) {
                btn.textContent = "Confirm: deposit " + fmtMoney(amount);
            } else if (amount <= 0) {
                btn.textContent = "Nothing to deposit: wallet is within your " + fmtMoney(keep) + " keep";
            } else {
                btn.textContent = "Deposit " + fmtMoney(amount) + " to the faction vault";
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
            var c = findConfirm();
            if (c) {
                busyUntil = Date.now() + 2500;
                c.click();
                render();
                return;
            }
            var f = form();
            var amount = depositAmount(walletNow());
            if (!f || amount <= 0) {
                return;
            }
            setMoney(f.input, f.hidden, amount);
            busyUntil = Date.now() + 600;
            // Torn enables DEPOSIT MONEY once it has seen the amount.
            setTimeout(function () {
                var again = form();
                if (again && usable(again.go)) {
                    again.go.click();
                } else {
                    note = "amount filled, press DEPOSIT MONEY yourself";
                }
                render();
            }, 400);
            render();
        });

        render();
        setInterval(render, 700);
        thLog("[TornHoarder] deposit mode v" + VER);
    }

    // ---- Items page: take the Ipecac Syrup you chose to take ---------------
    // Only shows up within 15 minutes of choosing it at the end of a run.
    function ipecacMode() {
        var asked = Number(readPref(IPECAC_KEY, "0"));
        if (!asked || Date.now() - asked > IPECAC_FRESH_MS) {
            return;
        }
        var btn = greenButton("th-ipecac-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);
        var taken = false;
        var busyUntil = 0;

        function row() {
            return document.querySelector('li[data-item="' + IPECAC_ID + '"]');
        }

        function render() {
            var r = row();
            var qty = r ? parseInt(r.getAttribute("data-qty"), 10) || 0 : 0;
            setLines(panel, [
                "Ipecac Syrup puts you in hospital for 60–90 minutes and adds 30 minutes of medical cooldown.",
                "Nobody can attack or mug you in hospital, so what sells meanwhile stays safe."
            ]);
            if (taken) {
                btn.textContent = "Ipecac taken ✓ (click to close)";
            } else if (Date.now() < busyUntil) {
                btn.textContent = "Using…";
            } else if (r && findConfirm(r)) {
                btn.textContent = "Confirm: use Ipecac Syrup";
            } else if (r) {
                btn.textContent = "Use Ipecac Syrup (" + qty + " left)";
            } else if (!/medical-items/.test(location.hash)) {
                btn.textContent = "Open your Medical items";
            } else {
                btn.textContent = "No Ipecac Syrup here: buy one on the Item Market →";
            }
        }

        btn.addEventListener("click", function () {
            if (Date.now() < busyUntil) {
                return;
            }
            if (taken) {
                dock.close();
                return;
            }
            var r = row();
            var c = r && findConfirm(r);
            if (c) {
                c.click();
                taken = true;
                writePref(IPECAC_KEY, "");
            } else if (r) {
                var use = r.querySelector('li[data-action="use"]');
                if (use) {
                    busyUntil = Date.now() + 1200;
                    use.click();
                }
            } else if (!/medical-items/.test(location.hash)) {
                location.hash = "#medical-items";
            } else {
                location.href = "https://www.torn.com/page.php?sid=ItemMarket#/market/view=search&itemID=" + IPECAC_ID;
            }
            render();
        });

        render();
        setInterval(render, 700);
        thLog("[TornHoarder] ipecac mode v" + VER);
    }

    // ---- What the buy modes have bought, per item, for a day ---------------
    // A purchase is counted the moment the script fills its amount and presses
    // BUY, whoever confirms it: Torn's confirm can't be relied on to be
    // recognised, and counting late is what let a restock keep buying. A
    // cancelled buy is then under-counted, which errs on buying too few.
    // Links carry when the app last checked stock (thchk / 4th field), so
    // only purchases since then count against that shortfall, across reloads.
    var BOUGHT_KEY = "tornhoarder.bought";
    var DAY_MS = 24 * 60 * 60 * 1000;

    function boughtLog() {
        try {
            return JSON.parse(localStorage.getItem(BOUGHT_KEY) || "{}") || {};
        } catch (e) {
            return {};
        }
    }

    function recordBought(id, qty) {
        var log = boughtLog();
        var now = Date.now();
        Object.keys(log).forEach(function (k) {
            log[k] = (log[k] || []).filter(function (x) { return now - x[0] < DAY_MS; });
        });
        (log[id] = log[id] || []).push([now, qty]);
        writePref(BOUGHT_KEY, JSON.stringify(log));
    }

    function boughtSince(id, sinceMs) {
        return (boughtLog()[id] || []).reduce(function (n, x) {
            return x[0] > sinceMs ? n + x[1] : n;
        }, 0);
    }

    // ---- City shop: buy what the app picked --------------------------------
    // The app links here with &thbuy=<id>:<qty>,... Click 1 fills one item's
    // amount and presses its BUY; click 2 confirms. One item per purchase.
    // `daily` (the day's shop plan, &thday=1): only buys since the latest
    // midnight-TCT reset count, so a plan made yesterday works again today.
    function buyMode(spec, checkedSec, daily) {
        var since = (Number(checkedSec) || 0) * 1000;
        if (daily) {
            since = Math.max(since, Math.floor(Date.now() / DAY_MS) * DAY_MS);
        }
        var want = {};
        spec.split(",").forEach(function (part) {
            var a = part.split(":");
            if (a[0] && Number(a[1]) > 0) {
                want[a[0]] = Number(a[1]);
            }
        });
        var pending = null;
        var busyUntil = 0;
        var note = "";
        var btn = greenButton("th-buy-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);

        function rows() {
            return Object.keys(want).map(function (id) {
                var input = document.querySelector('input[name="buyAmount[]"][id="' + id + '"]');
                var nameEl = document.getElementById(id + "-name");
                var stockEl = document.getElementById(id + "-stock");
                var priceEl = document.getElementById(id + "-price");
                var stock = stockEl ? parseInt((stockEl.textContent.match(/([0-9,]+)\\s*in stock/) || [0, (stockEl.textContent.match(/[0-9,]+/) || ["0"])[0]])[1].replace(/,/g, ""), 10) || 0 : 0;
                var price = priceEl ? parseInt(((priceEl.textContent.match(/\\$\\s*([0-9,]+)/) || [0, "0"])[1]).replace(/,/g, ""), 10) || 0 : 0;
                var left = Math.max(want[id] - boughtSince(id, since), 0);
                return {
                    id: id, name: nameEl ? nameEl.textContent.trim() : "item " + id, input: input,
                    stock: stock, price: price, qty: Math.min(left, stock), left: left
                };
            });
        }

        function render() {
            if (awayMessage()) {
                btn.textContent = awayMessage();
                return;
            }
            var all = rows();
            setLines(panel, ["Buying for resale (Torn allows 100 city-shop items a day, all shops together):"].concat(
                all.map(function (r) {
                    var got = boughtSince(r.id, since);
                    return (r.left === 0 ? "✓ " : "· ") + r.name + ": " + got + " of " + want[r.id] + " bought" +
                        (r.left && !r.input ? " (not in stock here now)" : r.left ? " · " + r.stock.toLocaleString("en-US") + " in stock" : "");
                })));
            var c = findConfirm();
            var next = all.filter(function (r) { return r.qty > 0 && r.input; })[0];
            if (Date.now() < busyUntil) {
                btn.textContent = "Buying…";
            } else if (c && pending) {
                btn.textContent = "Confirm: buy " + pending.qty + " × " + pending.name + (pending.price ? " · " + fmtMoney(pending.qty * pending.price) : "");
            } else if (next) {
                btn.textContent = "Buy " + next.qty + " × " + next.name + (next.price ? " · " + fmtMoney(next.qty * next.price) : "");
            } else {
                btn.textContent = all.some(function (r) { return r.left > 0; })
                    ? doneLabel("Nothing left to buy in stock here") : doneLabel("Bought ✓");
            }
            if (note) {
                btn.textContent += " — " + note;
            }
        }

        btn.addEventListener("click", function () {
            if (Date.now() < busyUntil || awayMessage()) {
                return;
            }
            note = "";
            var c = findConfirm();
            if (c && pending) {
                pending = null;
                busyUntil = Date.now() + 2000;
                c.click();
                render();
                return;
            }
            var next = rows().filter(function (r) { return r.qty > 0 && r.input; })[0];
            if (!next) {
                finishStop(dock);
                return;
            }
            var item = document.querySelector('span.item[itemid="' + next.id + '"]');
            var reveal = item && item.querySelector("button.buy-info");
            if (reveal) {
                reveal.click();   // opens the amount box
            }
            setField(next.input, next.qty);
            recordBought(next.id, next.qty);
            var wrap = next.input.closest(".buy-act-wrap") || next.input.parentElement;
            var buy = wrap && wrap.querySelector(".buy-act button");
            pending = next;
            if (!usable(buy)) {
                note = "amount filled, press BUY yourself";
                render();
                return;
            }
            busyUntil = Date.now() + 800;
            buy.click();
            render();
        });

        render();
        setInterval(render, 700);
        thLog("[TornHoarder] buy mode v" + VER + ": " + Object.keys(want).length + " item(s)");
    }

    // ---- Keep stocked: restock run ------------------------------------------
    // One trip through every stop the app's check lists (each shop, each Item
    // Market item, the ammo page), like the shop run: when a stop is done its
    // button becomes "Next: … →". State lives in this browser for 2 hours.
    var RESTOCK_KEY = "tornhoarder.restockRun";
    var RESTOCK_TTL_MS = 2 * 60 * 60 * 1000;

    function restockRun() {
        try {
            var r = JSON.parse(localStorage.getItem(RESTOCK_KEY) || "null");
            return r && r.stops && r.stops.length && Date.now() - r.started < RESTOCK_TTL_MS ? r : null;
        } catch (e) {
            return null;
        }
    }

    function startRestock(route, name) {
        if (!route || !route.length) {
            return false;
        }
        writePref(RESTOCK_KEY, JSON.stringify({ stops: route, i: 0, started: Date.now(), name: name || "Restock run" }));
        location.href = route[0].url;
        return true;
    }

    // A finished stop's button: where the next click goes.
    function doneLabel(base) {
        var r = restockRun();
        if (!r) {
            return base + " (click to close)";
        }
        var nxt = r.stops[r.i + 1];
        return base + (nxt ? " · Next: " + nxt.label + " →" : " · " + (r.name || "Restock run") + " done ✓ (click to close)");
    }

    function finishStop(dock) {
        var r = restockRun();
        if (!r || r.i + 1 >= r.stops.length) {
            try {
                localStorage.removeItem(RESTOCK_KEY);
            } catch (e) { /* storage blocked */ }
            dock.close();
            return;
        }
        r.i += 1;
        writePref(RESTOCK_KEY, JSON.stringify(r));
        location.href = r.stops[r.i].url;
    }

    // ---- Tray: every notice in one stack, bottom left ------------------------
    // One line per kind (below minimum, city shops, restocks abroad, listings
    // to pull); clicking one opens it and closes the others, so they never
    // cover each other. ⚙ turns kinds (and the restock chime) on or off.
    var TRAY_KEY = "tornhoarder.tray.";
    var TRAY_KINDS = [["stock", "Below minimum", "on"], ["city", "City-shop count", "on"],
                      ["restock", "Restocks abroad", "on"], ["chime", "Chime on a restock", "off"]];
    var trayHost = null;
    var trayOpen = "";
    var trayDraws = [];

    function trayOn(kind) {
        var def = TRAY_KINDS.filter(function (k) { return k[0] === kind; })[0];
        return readPref(TRAY_KEY + kind, def ? def[2] : "on") === "on";
    }

    function tray() {
        if (!trayHost) {
            trayHost = document.createElement("div");
            trayHost.id = "th-tray";
            trayHost.style.cssText = "position:fixed;left:12px;bottom:56px;z-index:999997;display:flex;" +
                "flex-direction:column;align-items:flex-start;gap:6px;max-width:360px;";
            document.body.appendChild(trayHost);
        }
        return trayHost;
    }

    function trayChip(id, css) {
        var chip = document.createElement("div");
        chip.id = id;
        chip.style.cssText = "max-width:360px;padding:5px 12px;border-radius:14px;color:#fff;" +
            "font:bold 13px/1.4 Arial,sans-serif;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.5);" + (css || "");
        tray().appendChild(chip);
        return chip;
    }

    function trayToggle(id) {
        trayOpen = trayOpen === id ? "" : id;
        trayDraws.forEach(function (f) { f(); });
    }

    function traySettings() {
        var box = trayChip("th-tray-settings", "background:rgba(0,0,0,.9);font-weight:normal;cursor:default;order:9;display:none;");
        var gear = trayChip("th-tray-gear", "background:rgba(0,0,0,.6);padding:2px 8px;font-weight:normal;order:10;");
        gear.textContent = "⚙";
        gear.title = "Choose which TornHoarder notices show";
        gear.addEventListener("click", function () { trayToggle("th-tray-settings"); });

        function draw() {
            box.style.display = trayOpen === "th-tray-settings" ? "block" : "none";
            box.textContent = "";
            TRAY_KINDS.forEach(function (k) {
                var line = document.createElement("div");
                line.textContent = (trayOn(k[0]) ? "☑ " : "☐ ") + k[1];
                line.style.cssText = "cursor:pointer;";
                line.addEventListener("click", function (ev) {
                    ev.stopPropagation();
                    var on = !trayOn(k[0]);
                    writePref(TRAY_KEY + k[0], on ? "on" : "off");
                    thLog("[TornHoarder] tray: " + k[1] + " " + (on ? "on" : "off"));
                    trayDraws.forEach(function (f) { f(); });
                });
                box.appendChild(line);
            });
        }
        trayDraws.push(draw);
        draw();
    }

    // ---- Keep stocked: badge on every Torn page -----------------------------
    // Shows what's below its minimum, from the app's latest check (fetched
    // live from the app, else as of when this script was built).
    function stockBadge() {
        var status = STOCK;
        var pill = trayChip("th-stock-badge", "background:rgba(183,28,28,.92);display:none;");
        pill.addEventListener("click", function (ev) {
            if (ev.target && ev.target.tagName === "A") {
                return;
            }
            trayToggle(pill.id);
        });

        function draw() {
            var short = (status && status.short) || [];
            var open = trayOpen === pill.id;
            pill.style.display = short.length && trayOn("stock") ? "block" : "none";
            if (!short.length) {
                return;
            }
            pill.textContent = "";
            var head = document.createElement("div");
            head.textContent = "⚠ " + short.length + " item(s) below minimum " + (open ? "▾" : "▸");
            pill.appendChild(head);
            if (!open) {
                return;
            }
            short.forEach(function (s) {
                var line = document.createElement("div");
                line.style.cssText = "font-weight:normal;margin-top:4px;";
                line.appendChild(document.createTextNode(s.name + ": " + s.have + " of " + s.min + " "));
                if (s.url) {
                    var a = document.createElement("a");
                    a.href = s.url;
                    a.textContent = "buy " + s.need + " →";
                    a.style.cssText = "color:#ffeb3b;";
                    line.appendChild(a);
                }
                pill.appendChild(line);
            });
            var route = status.route || [];
            if (route.length) {
                var all = document.createElement("div");
                all.textContent = "Restock all: " + route.length + " stop(s) →";
                all.style.cssText = "color:#ffeb3b;text-decoration:underline;margin-top:6px;";
                all.addEventListener("click", function (ev) {
                    ev.stopPropagation();
                    startRestock(route);
                });
                pill.appendChild(all);
            }
            var age = status.checked ? Math.round((Date.now() / 1000 - status.checked) / 60) : null;
            var foot = document.createElement("div");
            foot.style.cssText = "font-weight:normal;opacity:.8;margin-top:4px;";
            foot.textContent = age == null ? "" : "Checked " + age + " min ago by the app (Keep stocked → Check now).";
            pill.appendChild(foot);
        }

        function refresh() {
            if (typeof GM_xmlhttpRequest === "undefined") {
                return;
            }
            GM_xmlhttpRequest({
                method: "GET",
                url: CFG.reportUrl.replace(/shop-report$/, "stock-status"),
                onload: function (res) {
                    try {
                        status = JSON.parse(res.responseText);
                        draw();
                    } catch (e) { /* keep what we have */ }
                }
            });
        }
        trayDraws.push(draw);
        draw();
        refresh();
        setInterval(refresh, 120000);
    }

    // ---- City shops: 100 items a day, all shops together -------------------
    // Torn resets the limit at midnight Torn time (UTC). Every BUY pressed on a
    // city shop page counts, by hand or through a buy link. A BUY you then
    // cancel at "Are you sure?" still counts, so the badge has a "fix" link.
    var CITY_KEY = "tornhoarder.cityBuys";
    var CITY_CAP = 100;

    function dayStartMs(now) {
        return now - now % DAY_MS;
    }

    function cityLog() {
        try {
            var log = JSON.parse(localStorage.getItem(CITY_KEY) || "[]");
            return Array.isArray(log) ? log : [];
        } catch (e) {
            return [];
        }
    }

    function cityUsed() {
        var start = dayStartMs(Date.now());
        return cityLog().reduce(function (n, x) { return x[0] >= start ? n + x[1] : n; }, 0);
    }

    function recordCity(qty) {
        var start = dayStartMs(Date.now());
        var log = cityLog().filter(function (x) { return x[0] >= start; });
        log.push([Date.now(), qty]);
        writePref(CITY_KEY, JSON.stringify(log));
    }

    function watchCityBuys() {
        document.addEventListener("click", function (ev) {
            var b = ev.target && ev.target.closest ? ev.target.closest(".buy-act button") : null;
            var wrap = b && b.closest(".buy-act-wrap");
            var input = wrap && wrap.querySelector('input[name="buyAmount[]"]');
            var qty = input ? parseInt(input.value, 10) || 0 : 0;
            if (qty > 0) {
                recordCity(qty);
            }
        }, true);
    }

    function cityBadge() {
        var plan = { checked: 0, route: [] };
        var fixing = false;
        var pill = trayChip("th-city-badge", "cursor:default;");

        function link(text, onClick) {
            var a = document.createElement("span");
            a.textContent = text;
            a.style.cssText = "color:#ffeb3b;text-decoration:underline;cursor:pointer;margin-left:8px;";
            a.addEventListener("click", function (ev) {
                ev.stopPropagation();
                onClick();
            });
            return a;
        }

        function fix() {
            fixing = true;
            pill.textContent = "";
            pill.appendChild(document.createTextNode("🛒 Bought in city shops today: "));
            var box = document.createElement("input");
            box.type = "number";
            box.value = String(cityUsed());
            box.style.cssText = "width:56px;";
            pill.appendChild(box);
            pill.appendChild(link("save", function () {
                var n = Math.max(0, Math.min(CITY_CAP, parseInt(box.value, 10) || 0));
                writePref(CITY_KEY, JSON.stringify(n ? [[dayStartMs(Date.now()), n]] : []));
                fixing = false;
                draw();
            }));
        }

        function draw() {
            if (fixing) {
                return;
            }
            if (awayMessage() || !trayOn("city")) {
                pill.style.display = "none";
                return;
            }
            pill.style.display = "block";
            var used = Math.min(cityUsed(), CITY_CAP);
            var left = CITY_CAP - used;
            var ms = dayStartMs(Date.now()) + DAY_MS - Date.now();
            var when = Math.floor(ms / 3600000) + "h " + ("0" + Math.floor(ms % 3600000 / 60000)).slice(-2) + "m";
            pill.style.background = left === CITY_CAP ? "rgba(46,125,50,.92)" : "rgba(33,33,33,.92)";
            pill.textContent = "";
            pill.appendChild(document.createTextNode(
                left === CITY_CAP ? "🛒 100 city-shop buys available"
                    : left ? "🛒 City shops: " + left + " of 100 left · resets in " + when
                        : "🛒 City shops: all 100 used · resets in " + when));
            if (left === CITY_CAP && plan.route && plan.route.length) {
                pill.appendChild(link("Today's buys: " + plan.route.length + " shop(s) →", function () {
                    startRestock(plan.route, "Today's buys");
                }));
            }
            if (used) {
                pill.appendChild(link("fix", fix));
            }
        }

        if (typeof GM_xmlhttpRequest !== "undefined") {
            GM_xmlhttpRequest({
                method: "GET",
                url: CFG.reportUrl.replace(/shop-report$/, "shop-plan"),
                onload: function (res) {
                    try {
                        plan = JSON.parse(res.responseText);
                    } catch (e) { /* no plan */ }
                    draw();
                }
            });
        }
        trayDraws.push(draw);
        draw();
        setInterval(draw, 30000);
    }

    // ---- Item Market: buy back up to a minimum -----------------------------
    // From the Keep-stocked link: &thneed=<id>:<need>:<max price>. Click 1
    // fills the cheapest listing at or under the cap; click 2 confirms.
    function marketBuyMode(spec) {
        var a = spec.split(":");
        var id = a[0];
        var need = Number(a[1]) || 0;
        var cap = Number(a[2]) || Infinity;
        var since = (Number(a[3]) || 0) * 1000;
        var bought = function () { return boughtSince(id, since); };
        var pending = null;
        var busyUntil = 0;
        var note = "";
        var btn = greenButton("th-restock-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);

        function sellers() {
            var out = [];
            var rows = document.querySelectorAll("ul[class*=sellerList] li[class*=rowWrapper]");
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                var input = r.querySelector("input.input-money:not([type=hidden])");
                var priceEl = r.querySelector("[class*=price]");
                var availEl = r.querySelector("[class*=available]");
                if (!input || !priceEl) {
                    continue;
                }
                out.push({
                    el: r,
                    input: input,
                    hidden: r.querySelector("input.input-money[type=hidden]"),
                    buy: r.querySelector("button[class*=buyButton]"),
                    price: parseInt((priceEl.textContent || "").replace(/[^0-9]/g, ""), 10) || 0,
                    avail: parseInt((availEl ? availEl.textContent : "1").replace(/[^0-9]/g, ""), 10) || 0,
                    name: (input.getAttribute("aria-label") || "").replace(/ amount$/i, "") || "item"
                });
            }
            return out.sort(function (x, y) { return x.price - y.price; });
        }

        // While Torn asks about one seller's row, that row has no amount box,
        // so it drops out of sellers(): stay on it (moving on cancels Torn's
        // question) until Torn is done with it, or PENDING_MS passes.
        var PENDING_MS = 20000;
        var logged = false;

        function settle() {
            if (!pending) {
                return;
            }
            var age = Date.now() - pending.at;
            var row = pending.el;
            var asking = row && !row.querySelector("input.input-money:not([type=hidden])") &&
                Array.prototype.indexOf.call(document.querySelectorAll("ul[class*=sellerList] li[class*=rowWrapper]"), row) >= 0;
            if (age > PENDING_MS) {
                pending = null;
                note = "Torn didn't take it; check the page and try again";
            } else if (age > 1500 && !asking && !/are you sure/i.test(document.body.textContent || "")) {
                pending = null;
            }
        }

        function render() {
            settle();
            var left = Math.max(need - bought(), 0);
            var all = sellers();
            var next = all.filter(function (s) { return s.avail > 0 && s.price <= cap; })[0];
            var name = all.length ? all[0].name : "item";
            setLines(panel, [
                "Keep stocked: need " + need + " " + name + ", bought " + bought() + " so far.",
                "Never paying more than " + (cap === Infinity ? "any price" : fmtMoney(cap)) + " each (10% over market value)."
            ]);
            if (left <= 0) {
                btn.textContent = doneLabel("Restocked ✓");
            } else if (Date.now() < busyUntil) {
                btn.textContent = "Buying…";
            } else if (pending && rowConfirm(pending.el, pending.buy)) {
                btn.textContent = "Confirm: buy " + pending.qty + " × " + pending.name + " @ " + fmtMoney(pending.price);
            } else if (pending) {
                btn.textContent = "Confirm " + pending.qty + " × " + pending.name + " on Torn's page";
                if (!logged) {
                    logged = true;
                    logConfirmMiss("restock", pending.el);
                }
            } else if (next) {
                btn.textContent = "Buy " + Math.min(next.avail, left) + " × " + next.name + " @ " + fmtMoney(next.price) + " (cheapest)";
            } else if (all.length) {
                btn.textContent = "Cheapest is " + fmtMoney(all[0].price) + ", over your " + fmtMoney(cap) + " cap" +
                    (restockRun() ? " · click to skip →" : "");
            } else {
                btn.textContent = "Waiting for the listings to load…";
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
            if (need - bought() <= 0) {
                finishStop(dock);
                return;
            }
            settle();
            if (pending) {
                var c = rowConfirm(pending.el, pending.buy);
                if (c) {
                    pending = null;
                    busyUntil = Date.now() + 2000;
                    c.click();
                }
                render();
                return;
            }
            var next = sellers().filter(function (s) { return s.avail > 0 && s.price <= cap; })[0];
            if (!next) {
                if (restockRun() && sellers().length) {
                    finishStop(dock);  // everything is over the cap: skip it
                }
                return;
            }
            var qty = Math.min(next.avail, need - bought());
            setField(next.input, qty);
            recordBought(id, qty);
            if (next.hidden) {
                next.hidden.value = String(qty);
            }
            pending = { qty: qty, name: next.name, price: next.price, el: next.el, buy: next.buy, at: Date.now() };
            busyUntil = Date.now() + 500;
            setTimeout(function () {
                var row = sellers().filter(function (s) { return s.price === next.price && s.avail === next.avail; })[0];
                if (row && usable(row.buy)) {
                    pending.buy = row.buy;
                    row.buy.click();
                } else {
                    note = "amount filled, press BUY yourself";
                }
                render();
            }, 300);
            render();
        });

        render();
        setInterval(render, 700);
        thLog("[TornHoarder] restock mode v" + VER);
    }

    // ---- Big Al's ammo: buy Standard rounds up to a minimum ---------------
    // From the Keep-stocked link: ammo.php?thammo=<need>&thammoname=<name>.
    // Opens Torn's Buy for that ammo and fills the amount; you confirm in Torn.
    function ammoMode(need, ammoName) {
        var btn = greenButton("th-ammo-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);
        var opened = false;
        var note = "";

        function row() {
            var spans = document.querySelectorAll("span");
            for (var i = 0; i < spans.length; i++) {
                if (spans[i].children.length === 0 && (spans[i].textContent || "").trim() === ammoName) {
                    return spans[i].closest("[class*=rowWrap]");
                }
            }
            return null;
        }

        // The amount box Torn shows after Buy: a visible text/number input
        // that isn't ours, chat or search.
        function amountBox() {
            var inputs = document.querySelectorAll("input[type=text], input[type=number], input:not([type])");
            for (var i = 0; i < inputs.length; i++) {
                var el = inputs[i];
                if (el.offsetParent === null || el.closest("#th-dock") || /search|chat|message/i.test((el.placeholder || "") + (el.getAttribute("aria-label") || ""))) {
                    continue;
                }
                return el;
            }
            return null;
        }

        function render() {
            var r = row();
            if (r) {
                r.style.outline = "2px solid #43a047";
            }
            setLines(panel, ["Keep stocked: buy " + need.toLocaleString("en-US") + " Standard " + ammoName + "."]);
            btn.textContent = !r ? "Can't find " + ammoName + " on this page"
                : opened ? doneLabel("Amount filled: press Torn's Buy, then click here")
                : "Buy " + need.toLocaleString("en-US") + " × " + ammoName + " (Standard)";
            if (note) {
                btn.textContent += " — " + note;
            }
        }

        btn.addEventListener("click", function () {
            note = "";
            if (opened) {
                finishStop(dock);
                return;
            }
            var r = row();
            var buy = r && r.querySelector("button[class*=buyButton]");
            if (!buy) {
                return;
            }
            buy.click();
            setTimeout(function () {
                var box = amountBox();
                if (box) {
                    setField(box, need);
                    opened = true;
                } else {
                    note = "type " + need + " in Torn's box";
                }
                render();
            }, 500);
        });

        render();
        setInterval(render, 1000);
        thLog("[TornHoarder] ammo mode v" + VER);
    }

    // ---- Travel agency: fly where the app's planner says ------------------
    // The app links here with &thfly=<Country>. Click 1 picks the country on
    // the map; click 2 presses Travel, only once Torn's "Are you sure you want
    // to travel to <Country>…?" names it. Uses whatever flight type is chosen.
    function flyMode(country) {
        var busyUntil = 0;
        var btn = greenButton("th-fly-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);

        function root() {
            return document.getElementById("travel-root") || document.body;
        }

        function confirmText() {
            var m = /travel to ([A-Za-z ]+?) on an? [a-z ]+?flight\\?[^?]*?reach your destination in around ([^.]+)\\./.exec(root().textContent || "");
            return m ? { country: m[1], time: m[2] } : null;
        }

        function travelButton() {
            return Array.prototype.filter.call(root().querySelectorAll("button.torn-btn"), function (b) {
                return /^travel$/i.test((b.textContent || "").trim());
            })[0] || null;
        }

        function pin() {
            return document.querySelector('button[class*=pin___][aria-label="' + country + '"]');
        }

        // The destination dropdown beside the map ("To: <Country>"); a pin
        // click alone didn't always change the destination.
        function destOption() {
            var s = document.querySelector("select[class*=destDropdown]");
            var o = s && Array.prototype.filter.call(s.options || [], function (x) {
                return (x.textContent || "").trim() === "To: " + country;
            })[0];
            return o ? { select: s, value: o.value } : null;
        }

        var logged = false;

        function pick() {
            if (pin()) {
                pin().click();
            }
            var d = destOption();
            if (d && d.select.value !== d.value) {
                var proto = typeof HTMLSelectElement !== "undefined" &&
                    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
                if (proto && proto.set) {
                    proto.set.call(d.select, d.value);
                } else {
                    d.select.value = d.value;
                }
                d.select.dispatchEvent(new Event("change", { bubbles: true }));
            }
            setTimeout(function () {
                var c = confirmText();
                if (!logged && !(c && c.country === country)) {
                    logged = true;
                    thLog("[TornHoarder] fly: " + country + " not picked. Pin: " + !!pin() + " · dropdown: " +
                        (d ? d.select.value + "→" + d.value : "none") + " · page says: " + (c ? c.country : "no confirm text"));
                }
            }, 1500);
        }

        function render() {
            if (document.body.dataset.traveling === "true") {
                btn.textContent = "✈ On the way to " + country + ". The shop page there will have your buy button.";
                setLines(panel, []);
                return;
            }
            var c = confirmText();
            var ready = c && c.country === country;
            setLines(panel, [ready ? "Flight to " + country + ": about " + c.time + ", by the flight type picked on the page." :
                "The app's planner picked " + country + "."]);
            if (Date.now() < busyUntil) {
                btn.textContent = "Taking off…";
            } else if (ready) {
                btn.textContent = "✈ Travel to " + country + " (" + c.time + ")";
            } else if (pin() || destOption()) {
                btn.textContent = "Pick " + country;
            } else {
                btn.textContent = "Can't find " + country + " on this page";
            }
        }

        btn.addEventListener("click", function () {
            if (Date.now() < busyUntil) {
                return;
            }
            if (document.body.dataset.traveling === "true") {
                dock.close();
                return;
            }
            var c = confirmText();
            if (c && c.country === country) {
                var t = travelButton();
                if (usable(t)) {
                    busyUntil = Date.now() + 3000;
                    t.click();
                }
            } else {
                pick();
            }
            render();
        });

        render();
        setInterval(render, 700);
        thLog("[TornHoarder] fly mode v" + VER + ": " + country);
    }

    // ---- Abroad: fill your bag with what pays best -------------------------
    // ---- Restock alerts: an item abroad is back in stock -------------------
    // The app's server reads the foreign stock log for items that went from
    // sold out to in stock in the last hour. A chime and a pill when one is
    // worth a trip: a full bag of it clears RESTOCK_MIN at the app's prices.
    var BAG_KEY = "tornhoarder.bagSize";       // learned on the shop page abroad
    var CHIMED_KEY = "tornhoarder.restockChimed";
    var HIDDEN_KEY = "tornhoarder.restockHidden";
    var RESTOCK_MIN = 100000;

    function restockAlert() {
        var sell = {};
        var list = [];
        var pill = trayChip("th-restock-badge", "background:rgba(21,101,192,.95);display:none;");
        pill.addEventListener("click", function (ev) {
            if (ev.target && ev.target.tagName === "A") {
                return;
            }
            trayToggle(pill.id);
        });

        function keys(k) {
            return readPref(k, "").split(",").filter(Boolean);
        }

        function remember(k, add) {
            writePref(k, keys(k).concat(add).slice(-50).join(","));
        }

        function hits() {
            var bag = parseInt(readPref(BAG_KEY, "18"), 10) || 18;
            return list.map(function (r) {
                var each = (Number(sell[r.item_id]) || 0) - r.cost;
                return { r: r, key: r.code + ":" + r.item_id + ":" + r.seen, load: sell[r.item_id] ? each * Math.min(r.qty, bag) : 0 };
            }).filter(function (h) {
                return h.load >= RESTOCK_MIN && keys(HIDDEN_KEY).indexOf(h.key) < 0;
            }).sort(function (a, b) { return b.load - a.load; });
        }

        function chime() {
            try {
                var Ctx = window.AudioContext || window.webkitAudioContext;
                var ctx = new Ctx();
                [880, 1320].forEach(function (f, i) {
                    var o = ctx.createOscillator();
                    var g = ctx.createGain();
                    o.frequency.value = f;
                    g.gain.value = 0.15;
                    o.connect(g);
                    g.connect(ctx.destination);
                    o.start(ctx.currentTime + i * 0.18);
                    o.stop(ctx.currentTime + i * 0.18 + 0.15);
                });
            } catch (e) { /* no sound: the pill still shows */ }
        }

        function draw() {
            var found = hits();
            if (awayMessage() || !found.length || !trayOn("restock")) {
                pill.style.display = "none";
                return;
            }
            var fresh = found.filter(function (h) { return keys(CHIMED_KEY).indexOf(h.key) < 0; });
            if (fresh.length) {
                if (trayOn("chime")) {
                    chime();
                }
                remember(CHIMED_KEY, fresh.map(function (h) { return h.key; }));
                thLog("[TornHoarder] tray: back in stock abroad: " + fresh.map(function (h) {
                    return h.r.name + " in " + h.r.country + " +" + fmtMoney(h.load) + " a bag";
                }).join("; "));
            }
            var open = trayOpen === pill.id;
            pill.style.display = "block";
            pill.textContent = "";
            pill.appendChild(document.createTextNode("🔔 " + found.length + " back in stock abroad " + (open ? "▾" : "▸")));
            if (!open) {
                return;
            }
            found.slice(0, 3).forEach(function (h) {
                var line = document.createElement("div");
                line.style.cssText = "font-weight:normal;margin-top:4px;";
                var mins = Math.max(0, Math.round((Date.now() / 1000 - h.r.seen) / 60));
                line.appendChild(document.createTextNode(h.r.name + " in " + h.r.country + ": " + h.r.qty.toLocaleString("en-US") +
                    " in stock (" + mins + " min ago), +" + fmtMoney(h.load) + " a bag "));
                var a = document.createElement("a");
                a.href = "https://www.torn.com/page.php?sid=travel&thfly=" + encodeURIComponent(h.r.country);
                a.textContent = "Fly ✈";
                a.style.cssText = "color:#ffeb3b;";
                line.appendChild(a);
                pill.appendChild(line);
            });
            var x = document.createElement("span");
            x.textContent = "dismiss";
            x.style.cssText = "color:#ffeb3b;text-decoration:underline;cursor:pointer;font-weight:normal;";
            x.addEventListener("click", function (ev) {
                ev.stopPropagation();
                remember(HIDDEN_KEY, found.map(function (h) { return h.key; }));
                draw();
            });
            pill.appendChild(x);
        }

        function get(feed, done) {
            GM_xmlhttpRequest({
                method: "GET",
                url: CFG.reportUrl.replace(/shop-report$/, feed),
                onload: function (res) {
                    try {
                        done(JSON.parse(res.responseText));
                    } catch (e) { /* keep what we have */ }
                    draw();
                }
            });
        }

        function refresh() {
            get("travel-prices", function (p) { sell = p.sell || {}; });
            get("restocks", function (r) { list = r.restocks || []; });
        }

        if (typeof GM_xmlhttpRequest === "undefined") {
            return;
        }
        trayDraws.push(draw);
        refresh();
        setInterval(refresh, 120000);
    }

    // On the shop page abroad, ranks what's in stock by the app's sell prices
    // (Where to fly → Plan a trip). Click 1 fills the best item's amount (what
    // you can still carry and afford) and presses its Buy; click 2 confirms if
    // Torn asks. Then the next best, until the bag is full.
    function abroadMode() {
        var prices = { checked: 0, sell: {} };
        var pending = null;
        var busyUntil = 0;
        var note = "";
        var lastBuy = null;   // the row whose Buy was pressed last, by us or by hand
        var buyAt = 0;
        var bagSeen = null;   // items in the bag at the last look
        var bought = [];      // what landed in the bag this visit
        var btn = greenButton("th-abroad-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);

        function num(text) {
            return parseInt(String(text || "").replace(/[^0-9]/g, ""), 10) || 0;
        }

        function capacity() {
            var m = /purchased\\s+([0-9,]+)\\s*\\/\\s*([0-9,]+)\\s+items/i.exec(document.body.textContent || "");
            return m ? { bought: num(m[1]), max: num(m[2]) } : null;
        }

        function rowInfo(row) {
                var box = row.querySelector("input.input-money:not([type=hidden])");
                var img = row.querySelector("img");
                var id = img && (/\\/images\\/items\\/([0-9]+)\\//.exec(img.getAttribute("src") || "") || [])[1];
                if (!box || !id) {
                    return null;
                }
                var nameEl = row.querySelector("[class*=itemNameButton]");
                var priceEl = row.querySelector("[class*=displayPrice]");
                var stockEl = Array.prototype.filter.call(row.querySelectorAll("[class*=srOnly]"), function (s) {
                    return /stock/i.test(s.textContent);
                })[0];
                var cost = priceEl ? num(priceEl.textContent) : 0;
                var sell = Number(prices.sell[id]) || 0;
                return {
                    id: id, el: row, name: nameEl ? nameEl.textContent.trim() : "item " + id, box: box,
                    hidden: row.querySelector("input.input-money[type=hidden]"),
                    buy: row.querySelector("button[class*=buyButton]"),
                    cost: cost, stock: stockEl ? num(stockEl.parentElement.textContent) : 0, profit: sell - cost
                };
        }

        function rows() {
            var out = Array.prototype.map.call(document.querySelectorAll("[class*=row___]"), rowInfo);
            return out.filter(function (r) { return r; }).filter(function (r) { return r.profit > 0 && r.cost > 0; })
                .sort(function (a, b) { return b.profit - a.profit; });
        }

        // The most profitable load for the slots and cash left: buying the top
        // item first can spend the cash and leave slots empty that a cheaper
        // item would have filled (1 Bear Gall + 17 Hockey Sticks beats 2 Bear
        // Galls on $60k). Branch and bound over counts, best items first.
        var NODE_LIMIT = 200000;

        function bestLoad(all, left, cash) {
            var items = all.filter(function (r) { return r.stock > 0; }).slice(0, 8);
            var best = { profit: 0, qty: [] };
            var qty = [];
            var nodes = 0;
            (function go(i, slots, money, profit) {
                if (profit > best.profit) {
                    best = { profit: profit, qty: qty.slice() };
                }
                if (i >= items.length || slots <= 0 || ++nodes > NODE_LIMIT ||
                    profit + slots * items[i].profit <= best.profit) {
                    return;
                }
                var r = items[i];
                for (var n = Math.min(r.stock, slots, Math.floor(money / r.cost)); n >= 0; n--) {
                    qty[i] = n;
                    go(i + 1, slots - n, money - n * r.cost, profit + n * r.profit);
                }
                qty[i] = 0;
            })(0, left, cash, 0);
            return items.map(function (r, i) { return { row: r, qty: best.qty[i] || 0 }; })
                .filter(function (p) { return p.qty > 0; });
        }

        function greedyProfit(all, left, cash) {
            var profit = 0;
            all.forEach(function (r) {
                var n = Math.max(0, Math.min(r.stock, left, Math.floor(cash / r.cost)));
                profit += n * r.profit;
                left -= n;
                cash -= n * r.cost;
            });
            return profit;
        }

        var loggedPlan = "";

        function next(all) {
            var cap = capacity();
            var left = cap ? cap.max - cap.bought : 0;
            var wallet = walletNow();
            var cash = wallet ? wallet : Infinity;
            var load = left > 0 ? bestLoad(all, left, cash) : [];
            if (!load.length) {
                return null;
            }
            var desc = load.map(function (p) { return p.qty + " x " + p.row.name; }).join(" + ");
            if (desc !== loggedPlan) {
                loggedPlan = desc;
                var profit = load.reduce(function (s, p) { return s + p.qty * p.row.profit; }, 0);
                thLog("[TornHoarder] abroad: plan " + desc + " = +" + fmtMoney(profit) + " for " + left +
                    " slot(s) and " + (wallet ? fmtMoney(wallet) : "unknown cash") +
                    "; most profitable first would make +" + fmtMoney(greedyProfit(all, left, cash)));
            }
            return { row: load[0].row, qty: load[0].qty, then: load.slice(1) };
        }

        // Done buying: the button flies home. Click 1 presses Torn's own
        // fly-home control; click 2 confirms if Torn asks.
        var homeAt = 0;
        var homeLogged = false;

        function visibleControls() {
            return Array.prototype.filter.call(document.querySelectorAll("button, a"), function (b) {
                return b.offsetParent !== null && !/^th-/.test(b.id || "") && !/buyButton/.test(b.className || "");
            });
        }

        function homeControl() {
            return visibleControls().filter(function (b) {
                var t = (b.textContent || "").trim();
                return /^(travel|fly|return|go)\\b.*\\b(home|torn|back)\\b/i.test(t) || /^(back|return) to torn/i.test(t);
            })[0] || null;
        }

        // Torn asks "Are you sure you want to travel back to Torn? ...": its
        // confirm is the control inside that question, not any "Travel" link.
        function homeQuestion(el) {
            for (var up = 0; up < 6 && el; up++, el = el.parentElement) {
                if (/are you sure/i.test(el.textContent || "")) {
                    return el;
                }
            }
            return null;
        }

        function homeConfirm() {
            if (!homeAt || Date.now() - homeAt > PENDING_MS) {
                return null;
            }
            var all = document.querySelectorAll("button, a, input[type=button], input[type=submit], [role=button]");
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var t = (el.textContent || el.value || "").trim();
                if (el.offsetParent === null || el.closest("#th-dock") || t === homeClicked ||
                    !/^(yes|confirm|continue|travel|fly|ok)\\b/i.test(t)) {
                    continue;
                }
                if (homeQuestion(el)) {
                    return el;
                }
            }
            return null;
        }

        function logHome() {
            if (homeLogged) {
                return;
            }
            homeLogged = true;
            thLog("[TornHoarder] abroad: no fly-home control recognized. Buttons and links: " +
                visibleControls().map(function (b) { return (b.textContent || "").trim(); }).filter(Boolean).join(" | "));
        }

        // Clicked, but no question and no takeoff: the control wasn't the
        // right one (Torn's page abroad hasn't been seen live yet).
        var homeClicked = "";
        var confirmAt = 0;
        var confirmed = null;
        var homeFailLogged = false;

        function homeFailed() {
            if (!homeAt || document.body.dataset.traveling === "true") {
                return false;
            }
            return confirmAt ? Date.now() - confirmAt > 6000 : Date.now() - homeAt > 6000 && !homeConfirm();
        }

        function homeLabel(base) {
            if (homeFailed()) {
                if (!homeFailLogged) {
                    homeFailLogged = true;
                    var q = confirmed && homeQuestion(confirmed);
                    thLog("[TornHoarder] abroad: clicked \\"" + homeClicked + "\\"" +
                        (confirmed ? " then confirmed with <" + confirmed.tagName + "> \\"" +
                            (confirmed.textContent || confirmed.value || "").trim() + "\\"" : "") +
                        " but Torn didn't take off." + (q ? " Question: " + (q.outerHTML || q.textContent || "").slice(0, 1500) : "") +
                        " Buttons and links: " +
                        visibleControls().map(function (b) { return (b.textContent || "").trim(); }).filter(Boolean).join(" | "));
                }
                return base + " · Torn didn't take off: fly home from Torn's page (click to close)";
            }
            if (confirmAt) {
                return "Taking off…";
            }
            if (homeConfirm()) {
                return "✈ Confirm: fly home";
            }
            if (homeControl()) {
                return base + " · ✈ Fly home";
            }
            logHome();
            return base + " · fly home from Torn's page (click to close)";
        }

        // Why next() found nothing although profitable rows are listed.
        var whyLogged = false;

        function whyNothing(all, cap) {
            var wallet = walletNow();
            var why = !cap ? "can't read your bag count on this page" :
                all.every(function (r) { return r.stock <= 0; }) ? "all sold out" :
                wallet != null && all.every(function (r) { return wallet < r.cost; }) ? "not enough cash on hand" :
                "can't work out an amount";
            if (!whyLogged) {
                whyLogged = true;
                var m = /purchased[^.]{0,40}/i.exec(document.body.textContent || "");
                thLog("[TornHoarder] abroad: nothing to buy (" + why + "). Bag text: " + (m ? m[0] : "not found") +
                    " · wallet: " + wallet + " · rows: " + all.map(function (r) {
                        return r.name + " stock " + r.stock + " cost " + r.cost + " profit " + r.profit;
                    }).join("; "));
            }
            return why;
        }

        // After Buy, Torn swaps the row's controls for its "are you sure"
        // question, so the row no longer looks buyable. Until the bag count
        // goes up (or PENDING_MS passes) the button stays on that item and
        // looks for the confirm button, page-wide or inside the row.
        var PENDING_MS = 20000;
        var logged = false;

        var pressed = null;  // the confirm pressed for the pending buy

        function pendingConfirm() {
            return pending ? rowConfirm(pending.row.el, pending.row.buy) : findConfirm();
        }

        function logConfirm() {
            if (logged || !pending) {
                return;
            }
            logged = true;
            logConfirmMiss("abroad", pending.row.el);
        }

        // A buy counts once the bag count goes up, not when Buy is pressed:
        // that's the confirmation, and what goes in the trip log.
        function landed(n) {
            var r = lastBuy;
            pending = null;
            note = "";
            if (!r) {
                bought.push("✓ " + n + " item(s) added to your bag");
                return;
            }
            bought.push("✓ Bought " + n + " × " + r.name + " · " + fmtMoney(n * r.cost) +
                (r.profit > 0 ? " (+" + fmtMoney(n * r.profit) + " expected)" : ""));
            if (typeof GM_xmlhttpRequest !== "undefined") {
                GM_xmlhttpRequest({
                    method: "POST",
                    url: CFG.reportUrl.replace(/shop-report$/, "trip-buy"),
                    headers: { "Content-Type": "application/json" },
                    data: JSON.stringify({
                        ts: Math.floor(Date.now() / 1000), country: document.body.dataset.country || "",
                        item_id: Number(r.id), item_name: r.name, qty: n, cost: r.cost, sell: Number(prices.sell[r.id]) || 0
                    })
                });
            }
        }

        function watchBag(cap) {
            if (!cap) {
                return;
            }
            if (bagSeen != null && cap.bought > bagSeen) {
                landed(cap.bought - bagSeen);
            }
            bagSeen = cap.bought;
            writePref(BAG_KEY, String(cap.max));
        }

        document.addEventListener("click", function (ev) {
            var b = ev.target && ev.target.closest ? ev.target.closest("button[class*=buyButton]") : null;
            var row = b && b.closest("[class*=row___]");
            var info = row && rowInfo(row);
            if (info) {
                lastBuy = info;
                buyAt = Date.now();
            }
        }, true);

        function render() {
            var cap = capacity();
            watchBag(cap);
            var all = rows();
            var lines = [];
            if (!prices.checked) {
                lines.push("No prices yet: in the app, open Where to fly and click Plan a trip, then reload this page.");
            } else {
                var age = Math.round((Date.now() / 1000 - prices.checked) / 60);
                lines.push("Best buys here, by the app's prices from " + age + " min ago (profit each after tax):");
                all.slice(0, 6).forEach(function (r) {
                    lines.push("· " + r.name + ": +" + fmtMoney(r.profit) + " · " + r.stock.toLocaleString("en-US") + " in stock");
                });
                if (!all.length) {
                    lines.push("Nothing in stock here resells at a profit.");
                }
            }
            if (cap) {
                lines.push("Bag: " + cap.bought + " of " + cap.max + " bought.");
            }
            setLines(panel, lines.concat(bought));
            if (pending && Date.now() - buyAt > PENDING_MS) {
                thLog("[TornHoarder] abroad: buy of " + pending.qty + " x " + pending.row.name + " not taken. " +
                    (pressed ? "Pressed <" + pressed.tagName + "> " + (pressed.outerHTML || "").slice(0, 600) +
                        " · in: " + ((pressed.parentElement && pressed.parentElement.outerHTML) || "").slice(0, 1500)
                        : "No confirm found.") + " · row: " + ((pending.row.el && pending.row.el.outerHTML) || "").slice(0, 1500));
                pending = null;
                pressed = null;
                note = "Torn didn't take it; check the page and try again";
            }
            var c = pendingConfirm();
            var n = next(all);
            if (Date.now() < busyUntil) {
                btn.textContent = homeAt && !pending ? "Taking off…" : "Buying…";
            } else if (pending && c) {
                btn.textContent = "Confirm: buy " + pending.qty + " × " + pending.row.name + " · " + fmtMoney(pending.qty * pending.row.cost);
            } else if (pending) {
                // Never move on to another item while Torn may be asking about this one.
                btn.textContent = "Confirm " + pending.qty + " × " + pending.row.name + " on Torn's page";
                logConfirm();
            } else if (n) {
                btn.textContent = "Buy " + n.qty + " × " + n.row.name + " · " + fmtMoney(n.qty * n.row.cost) +
                    " (+" + fmtMoney(n.qty * n.row.profit) + ")" + (n.then.length ? " · then " +
                        n.then.map(function (p) { return p.qty + " × " + p.row.name; }).join(", ") : "");
            } else {
                btn.textContent = homeLabel(cap && cap.bought >= cap.max ? "Bag full ✓" :
                    all.length ? "Nothing to buy: " + whyNothing(all, cap) : "Nothing worth buying here");
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
            if (pending) {
                var c = pendingConfirm();
                if (c) {
                    busyUntil = Date.now() + 2000;
                    pressed = c;
                    c.click();
                }
                render();
                return;
            }
            var n = next(rows());
            if (!n) {
                if (confirmAt && !homeFailed()) {
                    render();  // confirmed: waiting for Torn to take off
                    return;
                }
                var yes = homeFailed() ? null : homeConfirm();
                var home = yes || (homeFailed() ? null : homeControl());
                if (!home) {
                    dock.close();
                    return;
                }
                if (yes) {
                    confirmAt = Date.now();
                    confirmed = yes;
                } else {
                    if (!homeAt || Date.now() - homeAt > PENDING_MS) {
                        homeAt = Date.now();
                    }
                    homeClicked = (home.textContent || "").trim();
                }
                busyUntil = Date.now() + 800;
                home.click();
                render();
                return;
            }
            setMoney(n.row.box, n.row.hidden, n.qty);
            pending = n;
            lastBuy = n.row;
            buyAt = Date.now();
            if (!usable(n.row.buy)) {
                note = "amount filled, press Buy yourself";
                render();
                return;
            }
            busyUntil = Date.now() + 800;
            n.row.buy.click();
            render();
        });

        if (typeof GM_xmlhttpRequest !== "undefined") {
            GM_xmlhttpRequest({
                method: "GET",
                url: CFG.reportUrl.replace(/shop-report$/, "travel-prices"),
                onload: function (res) {
                    try {
                        prices = JSON.parse(res.responseText);
                    } catch (e) { /* keep none */ }
                    render();
                }
            });
        }
        render();
        setInterval(render, 700);
        thLog("[TornHoarder] abroad mode v" + VER);
    }

    if (window.top !== window) {
        return;
    }

    stockBadge();
    cityBadge();
    restockAlert();
    traySettings();
    if (location.pathname.indexOf("/shops.php") === 0 || location.pathname.indexOf("/bigalgunshop.php") === 0) {
        watchCityBuys();
    }

    // The app's "Fly" link: the travel agency, set to go where the planner says.
    var flySpec = /[?&]thfly=([^&]+)/.exec(location.search);
    if (flySpec && location.pathname.indexOf("/page.php") === 0 && document.body.dataset.abroad !== "true") {
        flyMode(decodeURIComponent(flySpec[1]));
        return;
    }

    // The shop page abroad (Torn sends you there on landing).
    if (location.pathname.indexOf("/page.php") === 0 && /[?&]sid=travel(&|$)/.test(location.search) &&
            document.body.dataset.abroad === "true" && document.body.dataset.traveling !== "true") {
        abroadMode();
        return;
    }

    // The app's "Restock all" link: index.php?threstock=1 starts the run
    // from the app's latest check.
    if (/[?&]threstock=1/.test(location.search)) {
        var begin = function (status) {
            if (!startRestock((status && status.route) || [])) {
                thLog("[TornHoarder] restock: nothing to buy");
            }
        };
        if (typeof GM_xmlhttpRequest !== "undefined") {
            GM_xmlhttpRequest({
                method: "GET",
                url: CFG.reportUrl.replace(/shop-report$/, "stock-status"),
                onload: function (res) {
                    try {
                        begin(JSON.parse(res.responseText));
                    } catch (e) {
                        begin(STOCK);
                    }
                },
                onerror: function () { begin(STOCK); }
            });
        } else {
            begin(STOCK);
        }
        return;
    }

    var ammoSpec = /[?&]thammo=([0-9]+)&thammoname=([^&]+)/.exec(location.search);
    if (ammoSpec && location.pathname.indexOf("/ammo.php") === 0) {
        ammoMode(Number(ammoSpec[1]), decodeURIComponent(ammoSpec[2]));
        return;
    }

    var needSpec = /[?&]thneed=([0-9:]+)/.exec(location.search);
    if (needSpec && location.pathname.indexOf("/page.php") === 0) {
        marketBuyMode(needSpec[1]);
        return;
    }

    if (location.pathname.indexOf("/factions.php") === 0) {
        depositMode();
        return;
    }

    if (location.pathname.indexOf("/item.php") === 0) {
        ipecacMode();
        return;
    }

    // ---- Trades: what to give a trader, and listings to pull first ----------
    // The app's Sell plan writes /trade-plan. Torn's trade and listings pages
    // haven't been seen live yet, so each one's layout goes to the app's log
    // once; filling amounts comes after that.
    function fetchTradePlan(done) {
        if (typeof GM_xmlhttpRequest === "undefined") {
            done({ traders: [], pull: [] });
            return;
        }
        GM_xmlhttpRequest({
            method: "GET",
            url: CFG.reportUrl.replace(/shop-report$/, "trade-plan"),
            onload: function (res) {
                var plan = null;
                try {
                    plan = JSON.parse(res.responseText);
                } catch (e) { /* no plan */ }
                done(plan || { traders: [], pull: [] });
            },
            onerror: function () { done({ traders: [], pull: [] }); }
        });
    }

    var probedLayouts = {};

    function logLayout(what) {
        if (probedLayouts[what]) {
            return;
        }
        probedLayouts[what] = true;
        // The page's own content only: Torn's header and sidebar links crowd out the form otherwise.
        var scope = document.querySelector(".content-wrapper") || document;
        var controls = Array.prototype.map.call(scope.querySelectorAll("button, a, input, select, textarea, [role=button]"), function (b) {
            if (b.offsetParent === null || (b.closest && b.closest("#th-dock, #th-tray"))) {
                return "";
            }
            var t = (b.textContent || b.value || "").trim().slice(0, 40);
            return "<" + String(b.tagName || "").toLowerCase() + (b.type ? " type=" + b.type : "") +
                (b.name ? " name=" + b.name : "") + (b.className ? " class=" + String(b.className).slice(0, 60) : "") + ">" +
                (t ? " " + t : "");
        }).filter(Boolean).slice(0, 150);
        var forms = Array.prototype.map.call(scope.querySelectorAll("form"), function (f) {
            return (f.outerHTML || "").replace(/\\s+/g, " ").slice(0, 4000);
        });
        thLog("[TornHoarder] layout " + what + ": " + controls.join(" | ") +
            (forms.length ? " || forms: " + forms.join(" || ") : ""));
    }

    function hashParam(key) {
        var m = new RegExp("[#&?]" + key + "=([^&]*)").exec(location.hash || "");
        return m ? decodeURIComponent(m[1]) : "";
    }

    // Which planned items to leave out of the trade; kept across Torn's trade
    // steps (each one is a page load).
    var TRADE_SKIP_KEY = "tornhoarder.tradeSkip";

    // Types into a field and leaves it, as tabbing out would: Torn's older pages
    // (trade) only enable their buttons on keyup/blur, not on input.
    function setText(el, value) {
        var proto = String(el.tagName || "").toUpperCase() === "TEXTAREA" && typeof HTMLTextAreaElement !== "undefined"
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        var d = Object.getOwnPropertyDescriptor(proto, "value");
        if (el.focus) {
            el.focus();
        }
        if (d && d.set) {
            d.set.call(el, String(value));
        } else {
            el.value = String(value);
        }
        ["input", "keyup", "change"].forEach(function (t) {
            el.dispatchEvent(new Event(t, { bubbles: true }));
        });
        if (el.blur) {
            el.blur();
        }
        el.dispatchEvent(new Event("blur"));
        el.dispatchEvent(new Event("focusout", { bubbles: true }));
    }

    function today() {
        var d = new Date(Date.now());
        return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
    }

    function tradeMode() {
        var btn = greenButton("th-trade-btn");
        var panel = infoPanel();
        var dock = makeDock(btn, panel);
        var plan = { traders: [] };
        var statusLogged = false;
        var excluded = {};
        try {
            excluded = JSON.parse(readPref(TRADE_SKIP_KEY, "{}")) || {};
        } catch (e) { /* none skipped */ }
        var panelSig = "";
        var started = false;

        function saveSkips() {
            writePref(TRADE_SKIP_KEY, JSON.stringify(excluded));
        }

        // Torn's "Start a trade" form: the INITIATE TRADE button, and the
        // description box next to the player box.
        function initiate() {
            return buttonLabelled(/initiate trade/i);
        }

        function description(go) {
            var form = go.closest ? go.closest("form") : null;
            return form ? form.querySelector("textarea, input[type=text]:not([name=userID])") : null;
        }

        function picked(t) {
            return t.items.filter(function (i) { return !excluded[i.id]; });
        }

        var note = "";
        var filled = 0;      // items whose amount was filled on this Add items page
        var missing = [];    // picked items with no row there
        var lastStep = "";

        function buttonLabelled(re) {
            return Array.prototype.filter.call(document.querySelectorAll("input[type=submit], button"), function (b) {
                return re.test(b.value || b.textContent || "");
            })[0] || null;
        }

        function addButton() {
            return buttonLabelled(/^\\s*add to trade\\s*$/i);
        }

        // Which step Torn shows, from the page itself (the URL hash isn't always there).
        function step() {
            var st = initiate() ? "start"
                : document.querySelector("input[name=amount]") ? "add"
                    : hashParam("step") === "accept" || buttonLabelled(/please check your trade/i) ? "accept"
                        : document.querySelector(".accept-trade-button") || hashParam("step") === "view" ? "view" : "other";
            if (st !== lastStep) {
                lastStep = st;
                filled = 0;
                missing = [];
            }
            return st;
        }

        function tradeId() {
            var id = hashParam("ID");
            if (id) {
                return id;
            }
            var f = document.querySelector("form[action*='ID=']");
            var m = f && /ID=(\\d+)/.exec(f.getAttribute("action") || "");
            return m ? m[1] : "";
        }

        // Each step's markup once, so the next round can read amounts and money exactly.
        var probedHtml = {};

        function probeHtml(st) {
            if (probedHtml[st] || st === "start" || st === "other") {
                return;
            }
            probedHtml[st] = true;
            var scope = document.querySelector(".content-wrapper") || document.body;
            var html = st === "add"
                ? Array.prototype.slice.call(document.querySelectorAll("input[name=amount]"), 0, 3).map(function (i) {
                    var row = i.closest ? i.closest("li") || i.parentElement : i.parentElement;
                    return (row && row.outerHTML) || "";
                }).join(" || ")
                : (scope.outerHTML || "");
            thLog("[TornHoarder] trade html " + st + ": " + html.replace(/<script[\\s\\S]*?<\\/script>/gi, "")
                .replace(/\\s+/g, " ").slice(0, 8000));
        }

        // Add items rows: the amount box, and the item by its image (/images/items/<id>/) or name.
        function addRows() {
            return Array.prototype.map.call(document.querySelectorAll("input[name=amount]"), function (input) {
                var row = (input.closest && input.closest("li")) || input.parentElement;
                if (!row) {
                    return null;
                }
                var img = row.querySelector ? row.querySelector("img[src*='/images/items/']") : null;
                var m = img && /items\\/(\\d+)\\//.exec(img.getAttribute("src") || "");
                return { input: input, id: m ? Number(m[1]) : null, text: String(row.textContent || "").toLowerCase() };
            }).filter(Boolean);
        }

        function fillAmounts(t) {
            var rows = addRows();
            filled = 0;
            missing = [];
            picked(t).forEach(function (i) {
                var r = rows.filter(function (x) { return x.id === Number(i.id); })[0] ||
                    rows.filter(function (x) { return x.id == null && x.text.indexOf(i.name.toLowerCase()) >= 0; })[0];
                if (!r) {
                    missing.push(i.name);
                    return;
                }
                setText(r.input, i.qty);
                filled += 1;
            });
            thLog("[TornHoarder] trade: filled " + filled + " of " + picked(t).length + " item(s) from " + rows.length +
                " rows" + (missing.length ? "; not found: " + missing.join(", ") : ""));
        }

        // On the trade view: is the item on the page (your side lists it by name)?
        function inTrade(i) {
            var scope = document.querySelector(".content-wrapper") || document.body;
            return String((scope && scope.textContent) || "").toLowerCase().indexOf(i.name.toLowerCase()) >= 0;
        }

        // The trade's later steps drop userID from the URL: remember who it's with.
        function trader() {
            var id = hashParam("userID");
            if (id) {
                writePref("tornhoarder.tradeWith", id);
            } else {
                id = readPref("tornhoarder.tradeWith", "");
            }
            var all = plan.traders || [];
            return all.filter(function (t) { return String(t.id) === String(id); })[0] || null;
        }

        function draw() {
            logLayout("trade " + (hashParam("step") || "?"));
            var t = trader();
            if (!t) {
                btn.textContent = "Trade: nothing planned for this player (click to close)";
                setLines(panel, ["The app's Sell plan lists what each of your traders pays more for than a quick sale."]);
                return;
            }
            var s = t.status || {};
            var sel = picked(t);
            var total = sel.reduce(function (n, i) { return n + i.qty * i.price; }, 0);
            var st = step();
            probeHtml(st);
            if (s.open === false) {
                btn.textContent = "⛔ " + t.name + " can't trade now: " + s.status + " (click to close)";
            } else if (!sel.length) {
                btn.textContent = "Nothing selected to trade (click to close)";
            } else if (st === "start") {
                btn.textContent = started ? "Starting the trade…" + (note ? " — " + note : "")
                    : "Start trade with " + t.name + ": " + sel.length + " item(s) · " + fmtMoney(total);
            } else if (st === "add") {
                btn.textContent = filled ? "Add to trade: " + filled + " item(s) filled — press ADD TO TRADE" +
                    (missing.length ? " (not found: " + missing.join(", ") + ")" : "")
                    : "Fill " + sel.length + " item(s) for " + t.name;
            } else if (st === "view") {
                var out = sel.filter(function (i) { return !inTrade(i); });
                btn.textContent = out.length ? "Add " + out.length + " item(s) to the trade →"
                    : "All " + sel.length + " item(s) are in: check the money from " + t.name + " (" + fmtMoney(total) +
                        "), then Accept Trade on Torn's page (click to close)";
            } else if (st === "accept") {
                btn.textContent = "Check it's " + fmtMoney(total) + " from " + t.name + ", then ACCEPT on Torn's page (click to close)";
            } else {
                btn.textContent = "Give " + t.name + " " + sel.length + " item(s) · " + fmtMoney(total) + " (click to close)";
            }
            var head = s.open === false
                ? t.name + " can't accept a trade while " + s.status.charAt(0).toLowerCase() + s.status.slice(1) +
                    (s.last ? " (last action " + s.last + ")" : "") + ". Hold these for when they're back:"
                : "Trade to " + t.name + " (" + sel.length + " of " + t.items.length + " items, " + fmtMoney(total) +
                    "). Click a line to leave it out:";
            if (s.open === false && !statusLogged) {
                statusLogged = true;
                thLog("[TornHoarder] trade: " + t.name + " unavailable: " + s.status);
            }
            var sig = head + "|" + t.items.map(function (i) { return i.id + ":" + !!excluded[i.id]; }).join(",");
            if (sig === panelSig) {
                return;
            }
            panelSig = sig;
            panel.textContent = "";
            var h = document.createElement("div");
            h.textContent = head;
            h.style.cssText = "color:#9ccc65;margin-bottom:4px;";
            panel.appendChild(h);
            selectLinks(panel, excluded, t.items.map(function (i) { return i.id; }), function () {
                saveSkips();
                draw();
            });
            t.items.forEach(function (i) {
                var line = document.createElement("div");
                line.textContent = (excluded[i.id] ? "✕ " : "✓ ") + Number(i.qty).toLocaleString("en-US") + " × " + i.name +
                    " at " + fmtMoney(i.price) + " = " + fmtMoney(i.qty * i.price);
                line.style.cssText = "cursor:pointer;" + (excluded[i.id] ? "text-decoration:line-through;color:#888;" : "");
                line.addEventListener("click", function () {
                    excluded[i.id] = !excluded[i.id];
                    saveSkips();
                    draw();
                });
                panel.appendChild(line);
            });
        }

        // Start: fill the description (today's date), leave the box, then press
        // INITIATE TRADE once Torn enables it. View: go to Add items. Add: click
        // 1 fills each picked item's amount, click 2 presses ADD TO TRADE.
        // Accepting stays on Torn's page: check the money first.
        function pressWhenEnabled(go, tries) {
            if (!go.disabled) {
                go.click();
                return;
            }
            if (tries <= 0) {
                note = "Torn kept INITIATE TRADE disabled: press it on Torn's page";
                thLog("[TornHoarder] trade: INITIATE TRADE still disabled after filling the description");
                draw();
                return;
            }
            setTimeout(function () { pressWhenEnabled(go, tries - 1); }, 200);
        }

        btn.addEventListener("click", function () {
            var t = trader();
            var st = step();
            if (!t || (t.status || {}).open === false || !picked(t).length) {
                dock.close();
                return;
            }
            if (st === "start" && !started) {
                var go = initiate();
                var box = description(go);
                if (box) {
                    setText(box, "Trade " + today());
                }
                thLog("[TornHoarder] trade: initiating with " + t.name + " (" + picked(t).length + " item(s))" +
                    (box ? ", description filled" : ", no description box found") + (go.disabled ? ", waiting for INITIATE" : ""));
                started = true;
                pressWhenEnabled(go, 10);
            } else if (st === "add" && !filled) {
                fillAmounts(t);
            } else if (st === "add") {
                var add = addButton();
                thLog("[TornHoarder] trade: ADD TO TRADE pressed with " + filled + " item(s)" + (add ? "" : " (button not found)"));
                if (add) {
                    add.click();
                }
            } else if (st === "view" && tradeId() && picked(t).some(function (i) { return !inTrade(i); })) {
                location.hash = "#step=add&ID=" + tradeId();
            } else {
                dock.close();
                return;
            }
            draw();
        });
        fetchTradePlan(function (p) {
            plan = p;
            draw();
        });
        setInterval(draw, 2000);
    }

    // Item Market "your listings": the ones a trader pays more for than they net.
    function pullMode() {
        var pill = null;
        var plan = null;

        function draw() {
            if (!/viewListing/i.test(location.hash || "") || !plan || !(plan.pull || []).length) {
                if (pill) {
                    pill.style.display = "none";
                }
                return;
            }
            logLayout("viewListing");
            if (!pill) {
                pill = trayChip("th-pull", "background:rgba(0,0,0,.9);border:1px solid #c62828;");
                pill.addEventListener("click", function () { trayToggle(pill.id); });
                trayDraws.push(draw);
                if (!trayOpen) {
                    trayOpen = pill.id;  // the reason for this page: open at first
                }
            }
            pill.style.display = "block";
            if (trayOpen !== pill.id) {
                setLines(pill, ["↩ Pull " + plan.pull.length + " listing(s) for a trader ▸"]);
                return;
            }
            setLines(pill, ["↩ Pull these listings: a trader pays more than they net after tax ▾"].concat(
                plan.pull.map(function (r) {
                    return "· " + r.name + " ×" + r.amount + " at " + fmtMoney(r.price) + " → " + r.trader + " pays " +
                        fmtMoney(r.pays);
                })).concat(["Then Run the app again to add them to the trade."]));
        }

        fetchTradePlan(function (p) {
            plan = p;
            draw();
        });
        setInterval(draw, 2000);
    }

    if (location.pathname.indexOf("/trade.php") === 0) {
        tradeMode();
        return;
    }

    var buySpec = /[?&]thbuy=([0-9:,]+)/.exec(location.search);
    if (buySpec && (location.pathname.indexOf("/shops.php") === 0 || location.pathname.indexOf("/bigalgunshop.php") === 0)) {
        buyMode(buySpec[1], (/[?&]thchk=([0-9]+)/.exec(location.search) || [0, 0])[1], /[?&]thday=1/.test(location.search));
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
        pullMode();
    }
})();
"""

# Which version of the code above a built script carries, so the app can
# rebuild it after a code change without waiting for the next Run.
TEMPLATE_HASH = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()[:12]


def template_of(script: str) -> Optional[str]:
    """The template stamp in a built script, or None (built before stamps)."""
    m = re.search(r"^// TornHoarder template (\w+)$", script[:3000], re.M)
    return m.group(1) if m else None


DEFAULT_CFG: Dict[str, object] = {
    "priceBasis": "fair",
    "fillQty": True,
    "premiumThreshold": 50.0,
    "premiumMaxMult": 3.0,
    "dumpUndercutPct": 5.0,
    "topMinQuality": 50.0,
    "scanMs": 1500,
    "reportUrl": "http://127.0.0.1:9999/shop-report",
    "keepCash": 0,            # deposit offers leave this much in your wallet
    "ipecacOver": 5_000_000,  # offer Ipecac after selling/listing at least this much (0 = never)
}


def build_userscript(
    price_map: Dict[int, Dict[str, object]],
    base_stats: Dict[int, Dict[str, object]],
    cfg: Optional[Dict[str, object]] = None,
    shop_run: Optional[Dict[str, object]] = None,
    stock: Optional[Dict[str, object]] = None,
) -> str:
    merged_cfg = {**DEFAULT_CFG, **(cfg or {})}
    return (
        TEMPLATE.replace("__PRICE_MAP__", json.dumps({str(k): v for k, v in price_map.items()}))
        .replace("__BASE_STATS__", json.dumps({str(k): v for k, v in base_stats.items()}))
        .replace("__CFG__", json.dumps(merged_cfg))
        # "built" tells one run's sales record apart from the next one's.
        .replace("__SHOP__", json.dumps(dict(shop_run or {"route": [], "items": {}}, built=int(time.time()))))
        .replace("__STOCK__", json.dumps(stock or {"checked": 0, "short": []}))
        .replace("__VERSION__", __version__)
        # Each build gets its own version (app version + build time), so
        # Tampermonkey sees every rebuild as an update instead of a reinstall.
        .replace("__BUILD__", str(int(time.time())))
        .replace("__TEMPLATE__", TEMPLATE_HASH)
    )