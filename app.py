import os
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import plotly.express as px

ALL_HEROES = [
    "all-heroes",
    "ana","ashe","baptiste","bastion","brigitte","cassidy","doomfist","dva","echo",
    "genji","hanzo","illari","junkerqueen","junkrat","kiriko","lifeweaver","lucio",
    "mauga","mei","mercy","moira","orisa","pharah","ramattra","reaper","reinhardt",
    "roadhog","sigma","sojourn","soldier-76","sombra","symmetra","torbjorn","tracer",
    "widowmaker","winston","wrecking-ball","zarya","zenyatta"
]

DATA_DIR = "data"
SNAP_FILE = os.path.join(DATA_DIR, "snapshots.csv")

st.set_page_config(page_title="Overwatch Stat Tracker", page_icon="🎯", layout="wide")

# Streamlit is dark by default on many setups, but we can add a little OW vibe:
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; }
      div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

def battletag_to_player_id(btag: str) -> str:
    # Name#1234 -> Name-1234
    return btag.strip().replace("#", "-")

import time
import requests

def overfast_get(path: str, params=None, max_retries: int = 5):
    base = "https://overfast-api.tekrop.fr"
    url = f"{base}{path}"
    headers = {"User-Agent": "OW-Stats-Streamlit/1.0"}

    delay = 1.0
    for attempt in range(max_retries):
        r = requests.get(url, params=params or {}, headers=headers, timeout=20)

        if r.status_code != 429:
            r.raise_for_status()
            return r.json()

        retry_after = r.headers.get("Retry-After")
        wait_s = float(retry_after) if retry_after and retry_after.isdigit() else delay
        time.sleep(wait_s)

        delay = min(delay * 2, 30)

    raise requests.HTTPError(f"429 Too Many Requests (gave up after {max_retries} retries): {url}")

def get_summary(player_id: str):
    return overfast_get(f"/players/{player_id}/summary")

def get_stats(player_id: str, gamemode: str, platform: str | None, hero: str | None):
    params = {"gamemode": gamemode}
    if platform:
        params["platform"] = platform
    if hero:
        params["hero"] = hero
    return overfast_get(f"/players/{player_id}/stats", params=params)

def ensure_snap_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SNAP_FILE):
        df = pd.DataFrame(
            columns=[
                "timestamp",
                "battletag",
                "player_id",
                "gamemode",
                "platform",
                "hero",
                "games_played",
                "games_won",
                "games_lost",
                "time_played_sec",
                "eliminations",
                "deaths",
                "hero_damage_done",
                "healing_done",
            ]
        )
        df.to_csv(SNAP_FILE, index=False)

def load_snaps():
    ensure_snap_file()
    return pd.read_csv(SNAP_FILE)

def save_snaps(df: pd.DataFrame):
    df.to_csv(SNAP_FILE, index=False)

def pluck_num(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    try:
        return float(cur)
    except Exception:
        return None

def career_to_table(career_json: dict) -> pd.DataFrame:
    # career_json: { "all-heroes": {...}, "ana": {...}, ... }
    rows = []
    for hero_key, h in career_json.items():
        rows.append(
            {
                "hero": hero_key,
                "games_played": pluck_num(h, "game", "games_played"),
                "games_won": pluck_num(h, "game", "games_won"),
                "games_lost": pluck_num(h, "game", "games_lost"),
                "time_played_sec": pluck_num(h, "game", "time_played"),
                "eliminations": pluck_num(h, "combat", "eliminations"),
                "deaths": pluck_num(h, "combat", "deaths"),
                "hero_damage_done": pluck_num(h, "combat", "hero_damage_done"),
                "healing_done": pluck_num(h, "assists", "healing_done"),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["winrate"] = df.apply(
        lambda r: (r["games_won"] / (r["games_won"] + r["games_lost"]))
        if pd.notna(r["games_won"]) and pd.notna(r["games_lost"]) and (r["games_won"] + r["games_lost"]) > 0
        else None,
        axis=1,
    )
    df["time_played_min"] = df["time_played_sec"] / 60.0
    return df

st.title("🎯 Overwatch Stat Tracker")

with st.sidebar:
    st.header("Lookup")
    battletag = st.text_input("BattleTag", placeholder="Example: YourName#12345")
    gamemode = st.selectbox("Mode", ["competitive", "quickplay"], index=0)
    platform = st.selectbox("Platform (optional)", ["", "pc", "console"], index=0)
    hero = st.text_input("Hero filter (optional)", placeholder="e.g., ana (blank = all)")

    fetch = st.button("Fetch", type="primary")
    st.divider()
    save_all = st.button("Save Snapshot (all-heroes)")
    save_view = st.button("Save Snapshot (current view)")

    st.caption("If your Overwatch profile is private, third-party sources usually can't read stats.")

snaps = load_snaps()

import streamlit as st

@st.cache_data(ttl=60)
def cached_summary(pid: str):
    return get_summary(pid)

@st.cache_data(ttl=60)
def cached_stats(pid: str, gamemode: str, platform: str | None, hero: str | None):
    return get_stats(pid, gamemode, platform, hero)
# State
if "summary" not in st.session_state:
    st.session_state.summary = None
if "table" not in st.session_state:
    st.session_state.table = pd.DataFrame()
if "pid" not in st.session_state:
    st.session_state.pid = None

if fetch:
    if "last_fetch" not in st.session_state:
        st.session_state.last_fetch = 0.0

    cooldown = 5  # seconds
    import time

    if time.time() - st.session_state.last_fetch < cooldown:
        st.warning("Please wait a few seconds before fetching again.")
        st.stop()

    st.session_state.last_fetch = time.time()
    
    if not battletag.strip():
        st.error("Enter a BattleTag first.")
    else:
        pid = battletag_to_player_id(battletag)
        try:
            summary = cached_summary(pid)
            stats = cached_stats(pid, gamemode, platform or None, hero.strip() or None)
            table = career_to_table(stats)

            st.session_state.summary = summary
            st.session_state.table = table
            st.session_state.pid = pid
            st.success(f"Fetched {battletag} ({pid})")
        except requests.HTTPError as e:
            st.error(f"Fetch failed: {e}")
        except Exception as e:
            st.error(f"Fetch failed: {e}")

# Top: summary cards
colA, colB, colC, colD = st.columns(4)
s = st.session_state.summary
if s:
    colA.metric("User", s.get("username") or "—")
    colB.metric("Title", s.get("title") or "—")
    endorsement = (s.get("endorsement") or {}).get("level")
    colC.metric("Endorsement", endorsement if endorsement is not None else "—")
    privacy = s.get("privacy")
    colD.metric("Privacy", privacy if privacy is not None else "—")
else:
    colA.metric("User", "—")
    colB.metric("Title", "—")
    colC.metric("Endorsement", "—")
    colD.metric("Privacy", "—")

st.divider()

# Save buttons
def do_save(df_to_save: pd.DataFrame):
    if df_to_save.empty or not st.session_state.pid:
        st.warning("Fetch stats first.")
        return
    now = datetime.now(timezone.utc).isoformat()
    pid = st.session_state.pid
    bt = battletag
    plat = platform or ""
    df_out = df_to_save.copy()
    df_out["timestamp"] = now
    df_out["battletag"] = bt
    df_out["player_id"] = pid
    df_out["gamemode"] = gamemode
    df_out["platform"] = plat

    # Keep only snapshot columns
    keep = [
        "timestamp","battletag","player_id","gamemode","platform","hero",
        "games_played","games_won","games_lost","time_played_sec",
        "eliminations","deaths","hero_damage_done","healing_done"
    ]
    df_out = df_out[keep]

    updated = pd.concat([snaps, df_out], ignore_index=True)
    save_snaps(updated)
    st.success("Snapshot saved!")

if save_all:
    t = st.session_state.table
    if not t.empty:
        do_save(t[t["hero"] == "all-heroes"])
    else:
        st.warning("Fetch stats first.")

if save_view:
    t = st.session_state.table
    if not t.empty:
        do_save(t)
    else:
        st.warning("Fetch stats first.")

# Tabs
tab1, tab2 = st.tabs(["Heroes", "History"])

with tab1:
    st.subheader("Career stats table")
    t = st.session_state.table
    if t.empty:
        st.info("Fetch a player to see hero stats.")
    else:
        t2 = t.sort_values("time_played_sec", ascending=False)
        st.dataframe(
            t2[[
                "hero","games_played","games_won","games_lost","winrate","time_played_min",
                "eliminations","deaths","hero_damage_done","healing_done"
            ]],
            use_container_width=True,
        )

with tab2:
    st.subheader("Saved snapshots")
    if snaps.empty:
        st.info("No snapshots yet.")
    else:
        st.dataframe(snaps.sort_values("timestamp", ascending=False), use_container_width=True)

        df = snaps.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df_all = df[df["hero"] == "all-heroes"].dropna(subset=["timestamp"])

        if len(df_all) >= 2:
            df_all["winrate"] = df_all.apply(
                lambda r: (r["games_won"] / (r["games_won"] + r["games_lost"]))
                if pd.notna(r["games_won"]) and pd.notna(r["games_lost"]) and (r["games_won"] + r["games_lost"]) > 0
                else None,
                axis=1,
            )
            c1, c2 = st.columns(2)

            with c1:
                fig = px.line(df_all.sort_values("timestamp"), x="timestamp", y="winrate", markers=True)
                st.plotly_chart(fig, use_container_width=True)

            with c2:
                df_all["time_min"] = df_all["time_played_sec"] / 60.0
                fig = px.line(df_all.sort_values("timestamp"), x="timestamp", y="time_min", markers=True)
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("Add at least 2 all-heroes snapshots to see trends.")

