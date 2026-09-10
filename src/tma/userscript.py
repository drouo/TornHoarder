from __future__ import annotations

import json
from typing import Dict, Optional

TEMPLATE = """\
// ==UserScript==
// @name         TMA Autofill - Torn Item Market
// @namespace    torn-market-analyzer
// @version      0.2.2
// @description  Fills Add Listing prices from the TMA app using the market's quality curve for weapons/armor.
// @match        https://www.torn.com/page.php?sid=ItemMarket*
// @updateURL    http://localhost:9999/tma_autofill.user.js
// @downloadURL  http://localhost:9999/tma_autofill.user.js
// @run-at       document-idle
// ==/UserScript==

(function () {
    "use strict";

    var PRICE_MAP = __PRICE_MAP__;
    var BASE_STATS = __BASE_STATS__;
    var CFG = __CFG__;

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
        : "0.2.2";
    var STATS = { rows: 0, filled: 0, skipped: 0, unmatched: 0 };
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
            "TMA " + VER + " | items " + Object.keys(PRICE_MAP).length +
            " | rows " + STATS.rows + " | filled " + STATS.filled +
            " | skipped " + STATS.skipped + " | unmatched " + STATS.unmatched;
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
                console.log("[TMA] no price data for row: " + name);
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

        var curvePrice = entry.curve && quality != null
            ? interpolate(entry.curve, quality, CFG.priceBasis)
            : null;
        var price = curvePrice != null && curvePrice > 0 ? curvePrice : (entry[CFG.priceBasis] || null);
        if (price != null && quality != null && quality >= CFG.premiumThreshold && curvePrice == null) {
            price = Math.max(price, Math.round(price * premiumFor(quality)));
        }
        if (price != null && quality != null && quality >= CFG.premiumThreshold) {
            container.style.outline = "2px solid #ffd97a";
        }

        if (price != null && inputs.price && !inputs.price.value) {
            inputs.price.value = String(price);
            STATS.filled += 1;
            container.setAttribute("data-tma-filled", "1");
            console.log("[TMA] filled " + entry.name + " @" + price +
                " (q=" + (quality != null ? quality.toFixed(2) : "n/a") + ", " + qualitySource + ")");
        } else {
            STATS.skipped += 1;
            container.setAttribute("data-tma-skipped", "1");
        }
        if (price != null && CFG.fillQty && inputs.qty && !inputs.qty.value) {
            inputs.qty.value = "1";
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

    if (window.top !== window) {
        return;
    }

    console.log("[TMA] autofill v" + VER + " loaded, " + Object.keys(PRICE_MAP).length + " items priced");
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
    "scanMs": 1500,
}


def build_userscript(
    price_map: Dict[int, Dict[str, object]],
    base_stats: Dict[int, Dict[str, object]],
    cfg: Optional[Dict[str, object]] = None,
) -> str:
    merged_cfg = {**DEFAULT_CFG, **(cfg or {})}
    return (
        TEMPLATE.replace("__PRICE_MAP__", json.dumps({str(k): v for k, v in price_map.items()}))
        .replace("__BASE_STATS__", json.dumps({str(k): v for k, v in base_stats.items()}))
        .replace("__CFG__", json.dumps(merged_cfg))
    )