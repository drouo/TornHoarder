"""The app's frame: the sidebar (API key, the Torn script, restart), the
status board (what each tool last did, what to do next) and the section bar
that picks which tools show below it."""

from __future__ import annotations

import time

import streamlit as st

from tma import __version__
from tma.app_status import ICON, NEVER, STALE, built_at, jobs, script_build, script_running, script_version
from tma.autoupdate_server import last_running
from tma.config import clear_api_key, load_api_key, load_minimums, save_api_key

from .. import paths
from . import autofill_export

SECTIONS = ("Sell", "Restock", "Travel", "Help")
SECTION_HELP = {
    "Sell": "Price your items, plan what goes where, build the Torn script",
    "Restock": "Keep stocked, today's city-shop buys, restock watch",
    "Travel": "Where to fly, the trip log",
    "Help": "How the app works",
}


def render_sidebar() -> tuple[str, bool, bool]:
    """The API key every tool uses. Returns (key, remember, restart pressed)."""
    with st.sidebar:
        st.header("Settings")
        key = st.text_input(
            "Torn API key",
            value=st.session_state["api_key"],
            key="api_key_input",
            type="password",
            help="Needs the user → inventory permission for API inventory. Stored locally in "
            "~/.tma_config.json when \"Remember API key\" is on.",
        ).strip()
        remember = st.checkbox("Remember API key on this computer", value=bool(st.session_state["api_key"]),
                               key="remember_key")
        if key != st.session_state["api_key"]:
            st.session_state["api_key"] = key
            if remember and key:
                save_api_key(key)
        if not remember and load_api_key():
            clear_api_key()
        if not key:
            st.warning("Enter your API key: every tool needs it.")

        st.subheader("Torn script")
        # A code update rebuilds the script here, with the last Run's prices
        # and plans, so fixes don't wait for the next Run.
        state = autofill_export.refresh_userscript()
        built = script_version(autofill_export.AUTOFILL_PATH)
        server_ok, url = autofill_export._ensure_update_server()
        if not built:
            st.caption("Built on your first **Run**.")
        elif not server_ok:
            st.warning("The local script server couldn't start; download the script from Sell → Autofill script.")
        else:
            build = script_build(autofill_export.AUTOFILL_PATH)
            st.markdown(f"[**Install / update v{built} (built {built_at(build)}) in Tampermonkey**]({url})")
            if state == "needs-run":
                st.warning("The script was built by older code. **Run** once to rebuild it; after that, code "
                           "updates rebuild it automatically.")
            else:
                _script_running(build)
            if built != __version__:
                st.warning(f"Built as v{built}; this app is v{__version__}. **Run** to rebuild it, then update.")
            st.caption("Tampermonkey shows an update prompt. The app's live data (prices, plans, stock) reaches "
                       "the script through this local server while the app is open.")

        st.divider()
        restart = st.button("↻ Restart", key="restart", help="Clears this session's results and plans; "
                            "saved data and settings stay.")
    return key, remember, restart


@st.fragment(run_every=10)
def _script_running(build: str) -> None:
    """Re-checked every 10s, so an update shows as taken without touching the app."""
    level, text = script_running(build, last_running(autofill_export.AUTOFILL_PATH), time.time())
    {"ok": st.success, "old": st.warning, "unknown": st.info}[level](text)


def board_jobs() -> list:
    return jobs(
        time.time(), last_run=paths.LAST_RUN_PATH, script=autofill_export.AUTOFILL_PATH, app_version=__version__,
        stock_status=paths.STOCK_STATUS_PATH, has_minimums=bool(load_minimums()), shop_plan=paths.SHOP_PLAN_PATH,
        travel_prices=paths.TRAVEL_PRICES_PATH, foreign_updates=paths.FOREIGN_UPDATES_PATH,
    )


def current_section() -> str:
    return st.session_state.get("section_last", "Sell")


def _go(section: str) -> None:
    st.session_state["section_last"] = section


def render_status_board(slot, current: str, rev: int) -> None:
    """Draws into `slot` (an st.empty()), so it can be redrawn at the end of
    a run to show what that run just did. `rev` keeps widget keys unique."""
    found = board_jobs()
    todo = [j for j in found if j.todo]
    with slot.container(border=True):
        st.markdown(f"**Status** · {len(todo)} to do" if todo else "**Status** · all up to date ✅")
        for j in found:
            c1, c2, c3 = st.columns([3, 7, 2], vertical_alignment="center")
            c1.markdown(f"{ICON[j.state]} **{j.name}**")
            c2.markdown(j.detail + (f"  \n→ {j.todo}" if j.todo else ""))
            if j.todo and j.section != current:
                c3.button(f"{j.section} →", key=f"go_{j.name}_{rev}", on_click=_go, args=(j.section,))


def render_section_bar() -> str:
    found = board_jobs()
    flag = {s: any(j.section == s and j.state in (STALE, NEVER) and j.todo for j in found) for s in SECTIONS}
    # Buttons, not st.tabs: the status board can switch sections, and only
    # the chosen section renders.
    section = current_section()
    for col, name in zip(st.columns(len(SECTIONS)), SECTIONS):
        col.button(f"{name} •" if flag[name] else name, key=f"nav_{name}", width="stretch",
                   type="primary" if name == section else "secondary", on_click=_go, args=(name,))
    st.caption(SECTION_HELP[section] + (" · • marks a section with something to do." if any(flag.values()) else ""))
    return section
