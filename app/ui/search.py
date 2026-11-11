"""Item search for the editable result tables.

The tables' edits feed the whole sell plan (shop run, listings, the
userscript), so a search only narrows what's *shown*: the editor gets the
matching rows, and its edits are merged back into the full table by row
index. Each search term gets its own editor state, because data_editor
remembers edits by row position and the positions change with the filter.
"""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
import streamlit as st

SEARCH_KEY = "item_search"


def search_query() -> str:
    return (st.session_state.get(SEARCH_KEY) or "").strip()


def matching(df: pd.DataFrame, query: str, column: str = "Item") -> pd.Series:
    if not query:
        return pd.Series(True, index=df.index)
    return df[column].astype(str).str.contains(query, case=False, regex=False)


def searchable_editor(
    df: pd.DataFrame,
    key: str,
    style: Optional[Callable[[pd.DataFrame], object]] = None,
    **kwargs,
) -> pd.DataFrame:
    """st.data_editor over the rows matching the search; returns the full table with edits merged in."""
    query = search_query()
    mask = matching(df, query)
    view = df[mask]
    if query:
        st.caption(f"Showing {len(view)} of {len(df)} rows matching “{query}”.")
    out = st.data_editor(style(view) if style else view, key=f"{key}:{query.lower()}" if query else key, **kwargs)
    if not query:
        return out
    full = df.copy()
    full.loc[out.index, out.columns] = out
    return full
