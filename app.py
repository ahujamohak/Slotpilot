import os
import re
import math
import numpy as np
import pandas as pd
from datetime import datetime
from collections import Counter
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from google import genai
from google.genai import types
try:
    from groq import Groq
except ImportError:
    Groq = None

# ==========================================
# 0. PAGE CONFIG & CONNECTION MANAGEMENT
# ==========================================
st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-120b"

SESSION_STATE_WORKSHEET = "Live Session"
SESSION_LOG_WORKSHEET = "Session Log"
GAMBLE_WORKSHEET = "Gamble Log"

TAB_OPTIONS = [
    "🃏 Gamble Analyzer",
    "📊 Today's Priority Board",
    "📈 Overall Performance",
    "📋 Pre-Planned Execution Cards",
    "📝 Live Data Entry",
    "🤖 Interactive AI Agent",
    "🧺 Played Basket & Overrides"
]

SUITS = ["Hearts", "Diamonds", "Clubs", "Spades"]
SUIT_EMOJI = {"Hearts": "♥", "Diamonds": "♦", "Clubs": "♣", "Spades": "♠"}
SUIT_COLOR = {"Hearts": "Red", "Diamonds": "Red", "Clubs": "Black", "Spades": "Black"}
COLOR_EMOJI = {"Red": "🔴", "Black": "⚫"}

def suit_html(suit: str, size: str = "22px") -> str:
    color = "red" if SUIT_COLOR[suit] == "Red" else "#222"
    return f'<span style="color:{color}; font-size:{size}; font-weight:600;">{SUIT_EMOJI[suit]} {suit}</span>'

def color_html(color: str, size: str = "22px") -> str:
    col = "red" if color == "Red" else "#222"
    return f'<span style="color:{col}; font-size:{size}; font-weight:600;">{COLOR_EMOJI[color]} {color}</span>'

# ==========================================
# 0B. SESSION PERSISTENCE
# ==========================================
def load_persisted_state():
    try:
        df = conn.read(worksheet=SESSION_STATE_WORKSHEET, ttl="0")
        if df is None or df.empty:
            return None
        df.columns = [str(c).strip() for c in df.columns]
        return df.iloc[-1].to_dict()
    except Exception:
        return None

def persist_session_state():
    try:
        record = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Date": datetime.now().strftime("%m/%d/%Y"),
            "Current Bankroll": st.session_state.current_bankroll,
            "Starting Bankroll": st.session_state.session_start_bankroll,
            "Target Bankroll": st.session_state.session_target,
            "Selected Day": st.session_state.selected_day,
            "Strict Day Penalty": st.session_state.strict_day_penalty,
            "Played Basket": "|".join(st.session_state.played_basket),
        }
        conn.update(worksheet=SESSION_STATE_WORKSHEET, data=pd.DataFrame([record]))
        st.session_state.last_saved_ts = record["Timestamp"]
    except Exception as e:
        st.session_state.last_save_error = str(e)

def reset_all_state(wipe_persisted=True):
    st.session_state.played_basket = []
    st.session_state.display_limit = 30
    st.session_state.session_start_bankroll = 1000.0
    st.session_state.current_bankroll = 1000.0
    st.session_state.session_target = 1800.0
    st.session_state.active_tab = "🃏 Gamble Analyzer"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []
    st.session_state.selected_day = datetime.now().strftime("%A")
    st.session_state.last_saved_ts = None
    st.session_state.last_save_error = None
    st.session_state.gamble_sequence = []
    if wipe_persisted:
        persist_session_state()

if "played_basket" not in st.session_state:
    restored = load_persisted_state()
    today_str = datetime.now().strftime("%m/%d/%Y")
    if restored is not None and str(restored.get("Date", "")).strip() == today_str:
        basket_raw = str(restored.get("Played Basket", "") or "")
        st.session_state.played_basket = [s for s in basket_raw.split("|") if s]
        st.session_state.display_limit = 30
        st.session_state.session_start_bankroll = float(restored.get("Starting Bankroll", 1000.0) or 1000.0)
        st.session_state.current_bankroll = float(restored.get("Current Bankroll", 1000.0) or 1000.0)
        st.session_state.session_target = float(restored.get("Target Bankroll", 1800.0) or 1800.0)
        st.session_state.active_tab = "🃏 Gamble Analyzer"
        strict_raw = restored.get("Strict Day Penalty", True)
        st.session_state.strict_day_penalty = str(strict_raw).strip().lower() in ("true", "1", "yes")
        st.session_state.chat_messages = []
        st.session_state.selected_day = str(restored.get("Selected Day") or datetime.now().strftime("%A"))
        st.session_state.last_saved_ts = restored.get("Timestamp")
        st.session_state.last_save_error = None
        st.session_state.session_was_restored = True
    else:
        reset_all_state(wipe_persisted=False)
        st.session_state.session_was_restored = False

if "strict_day_penalty" not in st.session_state:
    st.session_state.strict_day_penalty = True
if "selected_day" not in st.session_state:
    st.session_state.selected_day = datetime.now().strftime("%A")
if "gamble_sequence" not in st.session_state:
    st.session_state.gamble_sequence = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "🃏 Gamble Analyzer"

def mark_slot_played(slot_name: str) -> str:
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)
        persist_session_state()
        return f"Successfully marked '{slot_name}' as played."
    return f"'{slot_name}' is already in the played basket."

def restore_slot(slot_name: str):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)
        persist_session_state()

# ==========================================
# 1. MASTER LIST & STRATEGY CONSTANTS
# ==========================================
STRATEGY_STEPS = [
    {"step": 1, "budget": 100, "denom": "$1.00", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 2, "budget": 100, "denom": "$0.10", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 3, "budget": 100, "denom": "$0.05", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 4, "budget": 100, "denom": "$0.02", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 5, "budget": 100, "denom": "$0.01", "bet": 5.00, "spins": "20+ (Dynamic)"},
]
STRATEGY_PLAN_SUMMARY = "5 Denoms ($1 → 10c → 5c → 2c → 1c) @ Fixed $5 Bet ($100 budget / denom)"

SLOT_MASTER_LIST = {
    "All Aboard The Lucky Link": ["Go West", "Shinobi"],
    "Balloon Link": ["Australian Outback", "Skull Island"],
    "Bau Zhu Zhao Fu": ["Blue Festival", "Red Festival"],
    "Bull Rush Blitz": ["Golden Empress", "Wild Outback", "Yarr Matey"],
    "Bull Rush Blitz 2 Multi": ["Maximus Money", "New York Nights", "Roses & Riches"],
    "Bull Rush Blitz 3 Multi": ["El Matador"],
    "Bull Rush Stampede": ["Fire Mountain", "Minotaur’s Treasure"],
    "Cash Horns": ["Cleopatra’s Kingdom", "Grand Toro", "Master Warrior", "Ragnar the Great"],
    "Cash Spark": ["Royal Spark"],
    "Choy's Kingdom": ["Lunar Festival"],
    "Dollar Storm": ["Aussie Boomer", "Caribbean Gold", "Egyptian Jewels", "Fight for Troy", "Ninja Moon"],
    "Dragon Cash": ["Genghis Khan", "Magic Panda"],
    "Dragon Link": ["Autumn Moon", "Genghis Khan", "Golden Century", "Golden Gong", "Happy & Prosperous", "Panda Magic", "Peace & Long Life", "Peacock Princess", "Silk Road", "Spring Festival"],
    "Dragon Rush": ["Battle Drum", "Shadow Clan", "Shaolin Style"],
    "Dragon Train": ["Chillin Wins", "Forever Emperor", "Khutulun Battle Princess", "Sun Shots"],
    "Eureka n more blastin": ["Eureka n more blastin"],
    "Fabulous Hold & Spin Jackpot": ["Cash Champ", "Come one, Come all", "Glitter & Glitz", "Magic Touch"],
    "Fireball": ["Sea Queen Express", "Shogun Express"],
    "Fortune Hearts": ["Emperor's Choice", "Fire Spell", "Lunar Dragon"],
    "Go for Grand": ["Golden Sombreros", "Outback Gold", "Power Charms"],
    "Golden Strike": ["Viking Vallhala"],
    "Grand Legends": ["Great King", "Magic Warrior", "Royal Emperor", "Sun Queen"],
    "Heaven & Earth": ["Lucky Pig", "Shaolin Ways", "Terracotta Emperor"],
    "Huff n Even More Puff": ["Huff n Even More Puff"],
    "Huff n More Puff": ["Huff n More Puff"],
    "Jewel of the Dragon": ["Red Phoenix"],
    "Lightning Link": ["Dragon's Riches", "Fire Idol", "Heart Throb", "High Stakes", "Magic Pearl", "Magic Totem", "Mine Mine Mine", "Moon Race", "Raging Bull", "Sahara Gold"],
    "Lock it Link": ["Bright Lights", "Cats, Hats and more Bats"],
    "Mystery of the Lamp": ["Enchanted Palace", "Treasure Oasis"],
    "Nugget Hunter": ["Sands of Fortunes"],
    "Outgrow Link": ["Eastern Moon", "Spooky Moon", "Western Moon"],
    "Piggy 'N' More": ["Bankin'"],
    "Portal Link": ["Wild Whale"],
    "Power Panther": ["Aztec Thunder", "Power Panther", "Tiki Tiki", "Wild Kingdom"],
    "Shenlong Unleashed": ["Fortune Town"],
    "Thunder Empire": ["Amazon Hearts", "Inca Diamonds", "King Samurai", "Magic Emperor"],
    "Ultra Shot Link": ["Sapphire Eyes"],
    "Where's the Gold": ["Where's the Gold"],
    "Wild Rumble": ["Shen Shan"]
}

# ==========================================
# 2. SHEET DATA INSPECTION & METRICS ENGINE
# ==========================================
@st.cache_data(ttl=15)
def load_and_inspect_sheet():
    try:
        df = conn.read(worksheet=SESSION_LOG_WORKSHEET, ttl="0")
        if df.empty:
            return pd.DataFrame(), []
        df.columns = [str(c).strip() for c in df.columns]
        return df, list(df.columns)
    except Exception:
        return pd.DataFrame(), []

def parse_spin_value(raw):
    """Parse 'Spin of feature hit' → integer (strips +). Returns None if invalid."""
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", ""):
        return None
    # remove trailing +
    if s.endswith("+"):
        s = s[:-1]
    try:
        return int(float(re.sub(r"[^\d.]", "", s)))
    except Exception:
        return None

def get_spins_needed(slot_name, family_name, live_df, percentile=85):
    """
    Calculate the spin count that covers ~85% of historical real hits.
    Outliers are naturally handled by using a high percentile.
    """
    if live_df.empty:
        return None

    cols = {str(c).lower().strip(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin")
    win_col = cols.get("win amount") or cols.get("win amount ($)") or cols.get("win")
    feat_col = cols.get("feature type")

    if not slot_col or not fam_col or not spin_col:
        return None

    df = live_df.copy()
    df = df[
        (df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
        (df[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
    ]
    if df.empty:
        return None

    # Keep only real hits
    if win_col:
        df["_win"] = pd.to_numeric(df[win_col].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")
        hits = df[df["_win"] > 0]
    elif feat_col:
        hits = df[df[feat_col].astype(str).str.lower() != "na"]
    else:
        hits = df

    if hits.empty:
        return None

    spins = hits[spin_col].apply(parse_spin_value).dropna().astype(int)
    if len(spins) < 3:
        return None

    # 85th percentile (covers the vast majority of wins, ignores extreme late outliers)
    value = int(np.percentile(spins, percentile))
    return value

def parse_session_log_data(live_df, slot_name, family_name):
    if live_df.empty:
        return pd.DataFrame()
    cols = {str(c).lower().strip(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin") or cols.get("spins")
    attempt_col = cols.get("attempt number") or cols.get("attempt")
    feature_num_col = cols.get("feature win number") or cols.get("feature number") or cols.get("hit number")
    hit_num_col = cols.get("hit number") or cols.get("hit")
    win_amt_col = cols.get("win amount") or cols.get("win amount ($)") or cols.get("win")
    mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    if not slot_col or not fam_col or not spin_col:
        return pd.DataFrame()
    df = live_df.copy()
    df = df[
        (df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
        (df[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
    ]
    if df.empty:
        return pd.DataFrame()
    def _parse_spin(raw):
        if pd.isna(raw):
            return np.nan, False
        s = str(raw).strip()
        is_censored = s.endswith("+")
        clean_s = s[:-1] if is_censored else s
        val = pd.to_numeric(re.sub(r"[^\d.]", "", clean_s), errors="coerce")
        return val, is_censored
    def _to_num(series):
        return pd.to_numeric(series.astype(str).str.extract(r"(\d+\.?\d*)")[0], errors="coerce")
    parsed_spins = df[spin_col].apply(_parse_spin)
    df["_spins"] = parsed_spins.apply(lambda x: x[0])
    df["_is_censored"] = parsed_spins.apply(lambda x: x[1])
    df["_attempt"] = _to_num(df[attempt_col]).fillna(1) if attempt_col else 1
    df["_feature_win_num"] = _to_num(df[feature_num_col]).fillna(0) if feature_num_col else 0
    df["_hit"] = _to_num(df[hit_num_col]).fillna(0) if hit_num_col else df["_feature_win_num"]
    df["_win"] = _to_num(df[win_amt_col]) if win_amt_col else 0.0
    df["_mult"] = _to_num(df[mult_col]) if mult_col else 0.0
    day_col = cols.get("day") or cols.get("day of week")
    df["_day"] = df[day_col].astype(str).str.strip() if day_col else ""
    return df

def compute_slot_rehit_metrics(slot_name, family_name, live_df):
    default_res = {
        "repeat_sample_size": 0, "attempt2_population": 0, "multi_hit_count": 0,
        "multi_hit_rate": 0.0, "avg_repeat_multiplier": 0.0, "max_repeat_multiplier": 0.0,
        "avg_attempt2_spins": 0.0, "repeat_recommendation": "No Repeat Data",
        "first_hit_count": 0, "first_hit_total": 0, "avg_first_multiplier": 0.0, "avg_first_spins": 0.0,
    }
    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
