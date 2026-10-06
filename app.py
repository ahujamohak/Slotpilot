import os
import re
import math
import numpy as np
import pandas as pd
from datetime import datetime
from collections import Counter, defaultdict
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
st.set_page_config(
    page_title="Slot Optimization & Execution Agent",
    layout="wide",
    initial_sidebar_state="auto",
)

# Safe CSS — do NOT override Streamlit sidebar width or metric label visibility
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1100px; }
    .stButton > button { min-height: 2.75rem; border-radius: 10px; font-weight: 600; }
    .sug-card {
        border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px;
        background: #ffffff; margin-bottom: 0.6rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .sug-card.ai { border-left: 4px solid #7c3aed; }
    .sug-card.stat { border-left: 4px solid #2563eb; }
    .session-banner {
        border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px 16px;
        background: #f8fafc; margin-bottom: 12px; font-size: 0.95rem; line-height: 1.45;
    }
    .session-banner b { font-weight: 700; }
    @media (max-width: 640px) {
        .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
    }
</style>
""", unsafe_allow_html=True)

conn = st.connection("gsheets", type=GSheetsConnection)

GEMINI_MODEL = "gemini-3.6-flash"  # updated from gemini-2.5-flash
GROQ_MODEL = "openai/gpt-oss-20b"    # updated from deprecated llama-3.1-8b-instant

SESSION_STATE_WORKSHEET = "Live Session"
SESSION_LOG_WORKSHEET = "Session Log"
GAMBLE_WORKSHEET = "Gamble Log"

TAB_OPTIONS = [
    "🎯 Live Decision",
    "🃏 Gamble Analyzer",
    "📊 Today's Priority Board",
    "🧺 Session & Basket",
    # Kept available via sidebar tools, not primary nav:
    # "🤖 Interactive AI Agent",
]

SUITS = ["Hearts", "Diamonds", "Clubs", "Spades"]
SUIT_EMOJI = {"Hearts": "♥", "Diamonds": "♦", "Clubs": "♣", "Spades": "♠"}
SUIT_COLOR = {"Hearts": "Red", "Diamonds": "Red", "Clubs": "Black", "Spades": "Black"}
COLOR_EMOJI = {"Red": "🔴", "Black": "⚫"}

RED_SUITS = ["Hearts", "Diamonds"]
BLACK_SUITS = ["Clubs", "Spades"]

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
    st.session_state.stop_win = 400.0          # lock profit / soft stop when +this
    st.session_state.stop_loss = 300.0         # hard stop when -this
    st.session_state.fade_gamble = True        # default ON – model has been anti-predictive
    st.session_state.active_tab = "🎯 Live Decision"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []
    st.session_state.selected_day = datetime.now().strftime("%A")
    st.session_state.last_saved_ts = None
    st.session_state.last_save_error = None
    st.session_state.gamble_sequence = []
    st.session_state.ai_priority_result = None
    st.session_state.ai_gamble_suggestion = None
    if wipe_persisted:
        persist_session_state()


def session_profit_status():
    """Return locked profit and traffic-light status for the session."""
    start = float(st.session_state.get("session_start_bankroll", 1000) or 1000)
    current = float(st.session_state.get("current_bankroll", 1000) or 1000)
    target = float(st.session_state.get("session_target", 1800) or 1800)
    stop_win = float(st.session_state.get("stop_win", 400) or 400)
    stop_loss = float(st.session_state.get("stop_loss", 300) or 300)
    pnl = current - start
    if pnl <= -stop_loss:
        status = "STOP_LOSS"
        message = f"Stop-loss hit (−${abs(pnl):.0f}). Walk. Session over."
    elif pnl >= stop_win:
        status = "LOCK_PROFIT"
        message = f"Profit lock zone (+${pnl:.0f}). Only A-tier machines or leave."
    elif current >= target:
        status = "TARGET_HIT"
        message = f"Target reached (${current:.0f}). Strongly consider leaving."
    elif pnl > 0:
        status = "AHEAD"
        message = f"Ahead +${pnl:.0f}. Protect it — no hero calls."
    else:
        status = "BEHIND"
        message = f"Behind ${pnl:.0f}. Stick to plan; do not chase."
    return {
        "pnl": pnl,
        "status": status,
        "message": message,
        "start": start,
        "current": current,
        "target": target,
        "stop_win": stop_win,
        "stop_loss": stop_loss,
    }

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
if "ai_gamble_suggestion" not in st.session_state:
    st.session_state.ai_gamble_suggestion = None
if "stop_win" not in st.session_state:
    st.session_state.stop_win = 400.0
if "stop_loss" not in st.session_state:
    st.session_state.stop_loss = 300.0
if "fade_gamble" not in st.session_state:
    st.session_state.fade_gamble = True
if "ai_priority_result" not in st.session_state:
    st.session_state.ai_priority_result = None

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

UPSIDE_BOOST = {
    "Shadow Clan": 2.10,
    "Emperor's Choice": 2.00,
    "Minotaur’s Treasure": 1.70,
    "Maximus Money": 1.55,
    "Battle Drum": 1.40,
    "Aztec Thunder": 1.25,
}

GRINDER_PENALTY = {
    "Amazon Hearts": 0.58,
    "Cleopatra’s Kingdom": 0.62,
    "Sands of Fortunes": 0.65,
    "Lunar Dragon": 0.72,
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
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", ""):
        return None
    if s.endswith("+"):
        s = s[:-1]
    try:
        return int(float(re.sub(r"[^\d.]", "", s)))
    except Exception:
        return None

def get_spins_for_hit(slot_name, family_name, live_df, hit_number=1, percentile=85):
    if live_df.empty:
        return None
    cols = {str(c).lower().strip(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin")
    hit_col = cols.get("hit number") or cols.get("hit")
    feat_win_col = cols.get("feature win number")
    if not slot_col or not fam_col or not spin_col:
        return None
    df = live_df.copy()
    df = df[
        (df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
        (df[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
    ]
    if df.empty:
        return None
    if hit_col:
        df["_hit"] = pd.to_numeric(df[hit_col], errors="coerce")
        hits = df[df["_hit"] == hit_number]
    elif feat_win_col:
        df["_hit"] = pd.to_numeric(df[feat_win_col], errors="coerce")
        hits = df[df["_hit"] == hit_number]
    else:
        return None
    if hits.empty:
        return None
    spins = hits[spin_col].apply(parse_spin_value).dropna().astype(int)
    if len(spins) < 2:
        return None
    value = int(np.percentile(spins, percentile))
    value = int(round(value * 1.20))
    return value

def get_recommended_checkin(spin_1st):
    if spin_1st is None:
        return 300
    if spin_1st <= 40:
        return 300
    elif spin_1st <= 60:
        return 350
    elif spin_1st <= 80:
        return 400
    elif spin_1st <= 100:
        return 450
    else:
        return 500

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
        "avg_third_multiplier": 0.0, "max_first_multiplier": 0.0,
    }
    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return default_res
    total_logs = len(parsed_df)
    for col in ["_feature_win_num", "_hit", "_attempt", "_mult", "_spins"]:
        if col in parsed_df.columns:
            parsed_df[col] = pd.to_numeric(parsed_df[col], errors="coerce")
    
    first_hits = parsed_df[(parsed_df["_feature_win_num"] == 1) & (parsed_df["_spins"].notna())]
    if first_hits.empty:
        first_hits = parsed_df[(parsed_df["_hit"] == 1) & (parsed_df["_attempt"] == 1) & (parsed_df["_spins"].notna())]
    first_hit_count = len(first_hits)
    avg_first_mult = round(float(first_hits["_mult"].mean()), 1) if not first_hits.empty else 0.0
    max_first_mult = round(float(first_hits["_mult"].max()), 1) if not first_hits.empty else 0.0
    valid_spins = first_hits["_spins"].dropna()
    avg_first_spins = round(float(valid_spins.mean()), 1) if not valid_spins.empty else 0.0

    repeat_entries = parsed_df[(parsed_df["_feature_win_num"] == 2)]
    if repeat_entries.empty:
        repeat_entries = parsed_df[(parsed_df["_hit"] == 2) & (parsed_df["_attempt"] == 2)]
    attempt2_rows = parsed_df[parsed_df["_attempt"] == 2]
    attempt2_population = len(repeat_entries) if attempt2_rows.empty and not repeat_entries.empty else len(attempt2_rows)
    repeat_count = len(repeat_entries)
    multi_hit_rate = round((repeat_count / attempt2_population) * 100.0, 1) if attempt2_population > 0 else 0.0
    avg_repeat_mult = round(repeat_entries["_mult"].mean(), 1) if not repeat_entries.empty else 0.0
    max_repeat_mult = round(repeat_entries["_mult"].max(), 1) if not repeat_entries.empty else 0.0
    att2_hits = repeat_entries[(repeat_entries["_spins"] > 0)]
    avg_att2_spins = round(att2_hits["_spins"].mean(), 1) if not att2_hits.empty else 0.0

    third_entries = parsed_df[(parsed_df["_feature_win_num"] == 3)]
    if third_entries.empty:
        third_entries = parsed_df[(parsed_df["_hit"] == 3)]
    avg_third_mult = round(third_entries["_mult"].mean(), 1) if not third_entries.empty else 0.0

    if attempt2_population == 0 and repeat_count == 0:
        recommendation = "ℹ️ UNTESTED REPEAT PROFILE"
    elif multi_hit_rate >= 40.0:
        recommendation = f"🔥 HIGH REPEAT POTENTIAL ({multi_hit_rate}%)"
    elif multi_hit_rate >= 20.0:
        recommendation = f"⚡ MODERATE REPEAT POTENTIAL ({multi_hit_rate}%)"
    else:
        recommendation = f"⚠️ LOW REPEAT POTENTIAL ({multi_hit_rate}%)"
    
    return {
        "repeat_sample_size": total_logs, "attempt2_population": attempt2_population,
        "multi_hit_count": repeat_count, "multi_hit_rate": multi_hit_rate,
        "avg_repeat_multiplier": avg_repeat_mult, "max_repeat_multiplier": max_repeat_mult,
        "avg_attempt2_spins": avg_att2_spins, "repeat_recommendation": recommendation,
        "first_hit_count": first_hit_count, "first_hit_total": total_logs,
        "avg_first_multiplier": avg_first_mult, "avg_first_spins": avg_first_spins,
        "avg_third_multiplier": avg_third_mult, "max_first_multiplier": max_first_mult,
    }

def compute_75_25_rvi(slot_name, family_name, live_df, target_day=None, strict_mode=True):
    baseline_score = 7.5
    if target_day is None:
        target_day = datetime.now().strftime("%A")
    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return baseline_score, "25% Baseline / 0 Logs", target_day, 1.0, 0, 0
    total_logs = len(parsed_df)
    day_log_count = 0
    day_factor = 1.0
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    target_idx = days_order.index(target_day) if target_day in days_order else 0
    nearby_days = {days_order[target_idx], days_order[(target_idx - 1) % 7], days_order[(target_idx + 1) % 7]}
    if "_day" in parsed_df.columns:
        day_matches = parsed_df[parsed_df["_day"].str.lower() == str(target_day).strip().lower()]
        day_log_count = len(day_matches)
        nearby_matches = parsed_df[parsed_df["_day"].str.strip().str.title().isin(nearby_days)]
        nearby_count = len(nearby_matches)
        if total_logs > 0:
            day_ratio = day_log_count / total_logs
            if day_log_count > 0:
                if day_ratio == 1.0 and day_log_count >= 2:
                    day_factor = 1.30
                elif day_ratio >= 0.5:
                    day_factor = 1.20
                elif day_ratio >= 0.25:
                    day_factor = 1.10
                else:
                    day_factor = 1.00
            elif nearby_count > 0:
                day_factor = 0.95 if nearby_count >= 3 else 0.85
            else:
                if strict_mode:
                    day_factor = 0.55 if total_logs >= 5 else (0.70 if total_logs >= 3 else 0.80)
                else:
                    day_factor = 0.90
    actual_hits = parsed_df[(parsed_df["_hit"] > 0)]
    hit_count = len(actual_hits)
    if hit_count == 0:
        final_rvi = round(baseline_score * day_factor, 2)
        return final_rvi, f"Day-Weighted Hybrid (0 hits, {day_log_count} {target_day} logs)", target_day, day_factor, day_log_count, total_logs
    hit_rate = hit_count / total_logs
    hit_rate_score = min(10.0, max(1.0, hit_rate * 10.0))
    avg_win_mult = actual_hits["_mult"].mean()
    win_magnitude_score = min(10.0, max(1.0, (avg_win_mult / 15.0) + 5.0))
    sheet_rvi = (0.40 * hit_rate_score) + (0.60 * win_magnitude_score)
    weighted_rvi = (0.75 * sheet_rvi) + (0.25 * baseline_score)
    final_rvi = round(min(10.0, max(1.0, weighted_rvi * day_factor)), 2)
    proof_str = f"75% Live Sheet ({hit_count}/{total_logs} hits, {day_log_count} on {target_day}s)"
    return final_rvi, proof_str, target_day, day_factor, day_log_count, total_logs

def _derive_jj_tendency(profile: dict) -> tuple:
    """
    From a behaviour profile, derive a short JJ tendency label and a recommended play style.
    Returns (jj_tendency: str, play_style: str, post_big_note: str)
    """
    if not profile or not profile.get("ok"):
        return "Unknown", "Insufficient data", "—"

    multi_rate = profile.get("overall_multi_hit_rate", 0) or 0
    post = profile.get("post_win", {})
    sample_q = profile.get("sample_quality", "Low")
    clustering = profile.get("clustering_score", 0.5)

    small = post.get("small", {})
    medium = post.get("medium", {})
    large = post.get("large", {})

    small_rate = small.get("rehit_rate")
    med_rate = medium.get("rehit_rate")
    large_rate = large.get("rehit_rate")
    large_med_spins = large.get("median_spins_to_rehit")
    small_med_spins = small.get("median_spins_to_rehit")

    # JJ Tendency
    if sample_q == "Low":
        jj = "Unknown"
    elif small_rate is not None and small_rate >= 45 and (small_med_spins is not None and small_med_spins <= 35):
        jj = "Aggressive JJ"
    elif (small_rate or 0) >= 35 or (med_rate or 0) >= 40:
        jj = "Selective JJ"
    elif multi_rate >= 40:
        jj = "Moderate repeat"
    elif multi_rate >= 25:
        jj = "Low repeat"
    else:
        jj = "Rarely repeats"

    # Post-big-win note
    if large_rate is None:
        post_big = "—"
    elif large_rate < 25:
        post_big = f"Walk after big (re-hit only {large_rate}%)"
    elif large_rate < 40:
        post_big = f"Caution after big ({large_rate}% re-hit)"
    else:
        spins_txt = f", ~{large_med_spins} spins" if large_med_spins else ""
        post_big = f"Can continue after big ({large_rate}%{spins_txt})"

    # Recommended play style
    if sample_q == "Low":
        style = "Test lightly"
    elif jj == "Aggressive JJ" and multi_rate >= 45:
        style = "Primary target – hunt + JJ"
    elif jj in ("Aggressive JJ", "Selective JJ") and multi_rate >= 35:
        style = "Strong – look for JJ spots"
    elif multi_rate >= 40 and clustering < 0.55:
        style = "Solid grinder with repeats"
    elif multi_rate < 25 and (large_rate is not None and large_rate < 30):
        style = "One-and-done – take profit"
    elif multi_rate < 30:
        style = "Selective only"
    else:
        style = "Standard"

    return jj, style, post_big


def build_priority_dataset(live_df, target_day=None, strict_mode=True):
    records = []
    slot_scores = []
    if target_day is None:
        target_day = datetime.now().strftime("%A")

    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rvi_score, source_proof, active_day, day_factor, day_hits, total_hits = compute_75_25_rvi(slot, fam, live_df, target_day, strict_mode)
            rehit = compute_slot_rehit_metrics(slot, fam, live_df)

            # Legacy spin estimates (kept for compatibility)
            spin_1st = get_spins_for_hit(slot, fam, live_df, hit_number=1, percentile=85)
            spin_2nd = get_spins_for_hit(slot, fam, live_df, hit_number=2, percentile=85)
            spin_3rd = get_spins_for_hit(slot, fam, live_df, hit_number=3, percentile=85)

            # New behaviour profile (KM-aware, censored, post-win)
            profile = build_slot_behaviour_profile(fam, slot, live_df)
            jj_tendency, play_style, post_big_note = _derive_jj_tendency(profile)

            # Prefer KM-aware budgets when available
            hn1 = profile.get("hit_numbers", {}).get(1, {}) if profile.get("ok") else {}
            hn2 = profile.get("hit_numbers", {}).get(2, {}) if profile.get("ok") else {}
            hn3 = profile.get("hit_numbers", {}).get(3, {}) if profile.get("ok") else {}

            budget_1st = hn1.get("km_p85") or hn1.get("p85") or spin_1st
            budget_2nd = hn2.get("km_p85") or hn2.get("p85") or spin_2nd
            budget_3rd = hn3.get("km_p85") or hn3.get("p85") or spin_3rd
            median_1st = hn1.get("median")
            sample_quality = profile.get("sample_quality", "Low") if profile.get("ok") else "Low"
            multi_rate = profile.get("overall_multi_hit_rate", rehit.get("multi_hit_rate", 0.0)) if profile.get("ok") else rehit.get("multi_hit_rate", 0.0)

            first_total = rehit.get("first_hit_total", 0) or 0
            first_hits = rehit.get("first_hit_count", 0) or 0
            avg_mult = rehit.get("avg_first_multiplier", 0.0) or 0.0
            max_mult = rehit.get("max_first_multiplier", 0.0) or 0.0
            avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0) or 0.0
            avg_3rd_mult = rehit.get("avg_third_multiplier", 0.0) or 0.0
            max_2nd_mult = rehit.get("max_repeat_multiplier", 0.0) or 0.0

            if first_total < 3:
                composite = 0.0
            else:
                success_rate = first_hits / first_total if first_total > 0 else 0
                success_score = min(10.0, success_rate * 8.5)

                mult_score = min(13.0, (avg_mult / 5.0) + (max_mult / 22.0))

                # Prefer new budget for spin score
                s1 = budget_1st if budget_1st is not None else spin_1st
                if s1 is None:
                    spin_score = 4.5
                elif s1 <= 40:
                    spin_score = 9.0
                elif s1 <= 55:
                    spin_score = 7.0
                elif s1 <= 70:
                    spin_score = 4.8
                elif s1 <= 90:
                    spin_score = 2.5
                else:
                    spin_score = 1.0

                multi_size_bonus = min(4.5,
                    (avg_2nd_mult / 16.0) +
                    (max_2nd_mult / 30.0) +
                    (avg_3rd_mult / 20.0) +
                    (multi_rate / 45.0)
                )

                # Bonus for strong JJ tendency
                jj_bonus = 0.0
                if jj_tendency == "Aggressive JJ":
                    jj_bonus = 1.1
                elif jj_tendency == "Selective JJ":
                    jj_bonus = 0.6

                # Realized EV proxy: hit_rate * avg_mult (what actually paid historically)
                ev_proxy = (first_hits / first_total) * avg_mult if first_total > 0 else 0.0
                ev_score = min(12.0, ev_proxy / 4.0)  # ~48 EV → 12

                # Sample confidence: prefer slots with more history
                sample_score = min(10.0, first_total / 5.0)

                composite = (
                    0.28 * ev_score +          # primary: realized feature EV
                    0.18 * mult_score +
                    0.12 * success_score +
                    0.12 * spin_score +
                    0.15 * multi_size_bonus +
                    0.08 * jj_bonus * 10 +
                    0.07 * sample_score
                )

                if first_total < 5:
                    composite *= 0.80
                elif first_total < 8:
                    composite *= 0.90
                elif first_total < 12:
                    composite *= 0.95

            # Soft manual tilts only (was 2.1x which dominated the board)
            if slot in UPSIDE_BOOST:
                composite *= min(1.25, 1.0 + (UPSIDE_BOOST[slot] - 1.0) * 0.25)
            if slot in GRINDER_PENALTY:
                composite *= max(0.75, GRINDER_PENALTY[slot])

            slot_scores.append({
                "family": fam,
                "slot": slot,
                "rvi": rvi_score,
                "composite": round(composite, 3),
                "source_proof": source_proof,
                "target_day": active_day,
                "day_factor": day_factor,
                "day_hits": day_hits,
                "total_hits": total_hits,
                "rehit_metrics": rehit,
                "spin_1st": spin_1st,
                "spin_2nd": spin_2nd,
                "spin_3rd": spin_3rd,
                # New fields
                "budget_1st": budget_1st,
                "budget_2nd": budget_2nd,
                "budget_3rd": budget_3rd,
                "median_1st": median_1st,
                "multi_hit_rate": multi_rate,
                "jj_tendency": jj_tendency,
                "play_style": play_style,
                "post_big_note": post_big_note,
                "sample_quality": sample_quality,
                "behaviour_profile": profile,
            })

    slot_scores = sorted(slot_scores, key=lambda x: x["composite"], reverse=True)

    for item in slot_scores:
        records.append({
            "family": item["family"],
            "slot": item["slot"],
            "base_rvi": item["rvi"],
            "composite": item["composite"],
            "checkin_alloc": 500.0,
            "strategy_plan": STRATEGY_PLAN_SUMMARY,
            "source_proof": item["source_proof"],
            "target_day": item["target_day"],
            "day_factor": item["day_factor"],
            "day_hits": item["day_hits"],
            "total_hits": item["total_hits"],
            "rehit_metrics": item["rehit_metrics"],
            "spin_1st": item["spin_1st"],
            "spin_2nd": item["spin_2nd"],
            "spin_3rd": item["spin_3rd"],
            "budget_1st": item["budget_1st"],
            "budget_2nd": item["budget_2nd"],
            "budget_3rd": item["budget_3rd"],
            "median_1st": item["median_1st"],
            "multi_hit_rate": item["multi_hit_rate"],
            "jj_tendency": item["jj_tendency"],
            "play_style": item["play_style"],
            "post_big_note": item["post_big_note"],
            "sample_quality": item["sample_quality"],
            "behaviour_profile": item["behaviour_profile"],
        })
    return records

# ==========================================
# 2C. LIVE DECISION ENGINE  (fully per-slot, data-driven)
# ==========================================
def _parse_spin_censored(raw):
    """Return (spin_value: float|None, is_censored: bool). 52+ → (52.0, True)"""
    if pd.isna(raw):
        return None, False
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", ""):
        return None, False
    is_censored = s.endswith("+")
    clean = s[:-1] if is_censored else s
    try:
        val = float(re.sub(r"[^\d.]", "", clean))
        return val, is_censored
    except Exception:
        return None, False


def _kaplan_meier_percentile(event_times, censored_times, pct=0.85):
    """
    Simple Kaplan-Meier estimator.
    event_times: list of spin counts where a hit occurred.
    censored_times: list of spin counts where player walked with no hit.
    Returns the smallest t such that estimated CDF >= pct.
    Falls back gracefully on tiny samples.
    """
    if not event_times and not censored_times:
        return None

    # All unique time points
    all_times = sorted(set([t for t in event_times + censored_times if t is not None and t > 0]))
    if not all_times:
        return None

    # At risk process
    n_events = len(event_times)
    n_cens = len(censored_times)
    total = n_events + n_cens
    if total < 2:
        # Too little data – return observed max hit or a conservative default
        if event_times:
            return int(max(event_times) * 1.15)
        return None

    # Build survival curve
    # S(t) = product over ti <= t of (1 - d_i / n_i)
    from collections import defaultdict
    deaths = defaultdict(int)
    cens = defaultdict(int)
    for t in event_times:
        deaths[t] += 1
    for t in censored_times:
        cens[t] += 1

    times = sorted(set(list(deaths.keys()) + list(cens.keys())))
    n_at_risk = total
    S = 1.0
    survival = {}
    for t in times:
        d = deaths.get(t, 0)
        c = cens.get(t, 0)
        if n_at_risk > 0 and d > 0:
            S *= (1.0 - d / n_at_risk)
        survival[t] = S
        n_at_risk -= (d + c)

    # Find smallest t where 1 - S(t) >= pct
    target = pct
    for t in times:
        cdf = 1.0 - survival[t]
        if cdf >= target:
            return int(round(t))

    # Never reached the percentile → extrapolate a little beyond last event
    last_event = max(event_times) if event_times else max(all_times)
    return int(round(last_event * 1.25))


def _safe_percentile(values, pct, default=None):
    vals = [v for v in values if v is not None and not np.isnan(v)]
    if len(vals) < 2:
        return default
    return float(np.percentile(vals, pct))


def build_slot_behaviour_profile(family_name, slot_name, live_df):
    """
    Fully data-driven behaviour profile for one Family+Slot.
    Handles right-censored (+) observations correctly.
    Returns a rich dict used by the decision engine.
    """
    parsed = parse_session_log_data(live_df, slot_name, family_name)
    if parsed.empty:
        return {"ok": False, "reason": "No data for this slot"}

    # Normalise numeric columns
    for col in ["_feature_win_num", "_hit", "_attempt", "_mult", "_spins"]:
        if col in parsed.columns:
            parsed[col] = pd.to_numeric(parsed[col], errors="coerce")

    # Re-parse spins with censoring flag from original raw if available
    # (parse_session_log_data already gives _spins and _is_censored)
    if "_is_censored" not in parsed.columns:
        parsed["_is_censored"] = False

    profile = {
        "ok": True,
        "family": family_name,
        "slot": slot_name,
        "total_logs": len(parsed),
        "hit_numbers": {},
        "overall_multi_hit_rate": 0.0,
        "clustering_score": 0.0,          # 0 = regular, 1 = extreme clustering
        "post_win": {},                   # conditional stats after first win size
        "sample_quality": "Low",
    }

    # ---------- Per-hit-number distributions ----------
    for hn in [1, 2, 3, 4]:
        # Hits of this number
        hit_mask = (parsed["_hit"] == hn) | (parsed["_feature_win_num"] == hn)
        hits = parsed[hit_mask & (parsed["_spins"].notna()) & (~parsed["_is_censored"])]
        event_spins = hits["_spins"].dropna().astype(float).tolist()

        # Censored walk-offs that occurred on an attempt that was trying for this hit number
        # (Attempt Number == hn and Hit Number == 0 and censored)
        cens_mask = (
            (parsed["_attempt"] == hn) &
            (parsed["_hit"] == 0) &
            (parsed["_is_censored"] == True) &
            (parsed["_spins"].notna())
        )
        censored_spins = parsed.loc[cens_mask, "_spins"].astype(float).tolist()

        # Also treat non-censored zeros? No – only explicit + are walk-offs.

        n_events = len(event_spins)
        n_cens = len(censored_spins)
        n_total = n_events + n_cens

        if n_total < 2:
            profile["hit_numbers"][hn] = {
                "n_events": n_events, "n_censored": n_cens, "n_total": n_total,
                "median": None, "p75": None, "p85": None, "p90": None,
                "km_p85": None, "avg_mult": None, "max_mult": None,
            }
            continue

        # Observed (uncensored) percentiles
        med = _safe_percentile(event_spins, 50)
        p75 = _safe_percentile(event_spins, 75)
        p85 = _safe_percentile(event_spins, 85)
        p90 = _safe_percentile(event_spins, 90)

        # Kaplan-Meier style 85th (accounts for walk-offs)
        km85 = _kaplan_meier_percentile(event_spins, censored_spins, pct=0.85)

        # Multipliers for actual hits
        mults = hits["_mult"].dropna().astype(float).tolist()
        avg_m = round(float(np.mean(mults)), 1) if mults else None
        max_m = round(float(np.max(mults)), 1) if mults else None

        profile["hit_numbers"][hn] = {
            "n_events": n_events,
            "n_censored": n_cens,
            "n_total": n_total,
            "median": int(med) if med is not None else None,
            "p75": int(p75) if p75 is not None else None,
            "p85": int(p85) if p85 is not None else None,
            "p90": int(p90) if p90 is not None else None,
            "km_p85": km85,
            "avg_mult": avg_m,
            "max_mult": max_m,
            "event_spins": event_spins,
            "censored_spins": censored_spins,
        }

    # ---------- Overall multi-hit rate (attempt 2 given attempt 1 existed) ----------
    att1 = parsed[parsed["_attempt"] == 1]
    att2 = parsed[parsed["_attempt"] == 2]
    # Prefer feature_win_num == 2 as the clean "second feature occurred"
    second_hits = parsed[(parsed["_feature_win_num"] == 2) | ((parsed["_hit"] == 2) & (parsed["_attempt"] == 2))]
    n_att2_pop = len(att2) if len(att2) > 0 else len(second_hits)
    n_second = len(second_hits)
    profile["overall_multi_hit_rate"] = round(n_second / n_att2_pop * 100, 1) if n_att2_pop > 0 else 0.0
    profile["n_second_hits"] = n_second
    profile["n_attempt2_pop"] = n_att2_pop

    # ---------- Clustering score ----------
    # Low variance of inter-hit gaps → regular; high variance → dry-spell + cluster
    gaps = []
    for hn in [1, 2, 3]:
        info = profile["hit_numbers"].get(hn, {})
        gaps.extend(info.get("event_spins", []))
    if len(gaps) >= 4:
        cv = float(np.std(gaps) / (np.mean(gaps) + 1e-6))
        # Map CV to 0-1-ish score
        profile["clustering_score"] = round(min(1.0, max(0.0, (cv - 0.4) / 1.2)), 2)
    else:
        profile["clustering_score"] = 0.5  # unknown

    # ---------- Post-win behaviour (JJ intelligence) ----------
    # Look at first-hit multipliers and what happened on the subsequent attempt
    first_hits = parsed[((parsed["_hit"] == 1) | (parsed["_feature_win_num"] == 1)) & (parsed["_mult"] > 0)]
    if len(first_hits) >= 3:
        mults = first_hits["_mult"].astype(float)
        q33 = float(mults.quantile(0.33))
        q66 = float(mults.quantile(0.66))

        def _bucket(m):
            if m <= q33:
                return "small"
            if m <= q66:
                return "medium"
            return "large"

        # For each first hit, see if a second hit followed and how many spins it took
        # We approximate by looking at rows that share the same session context.
        # Simple robust approach: use overall multi-hit rate conditioned on first mult bucket.
        post = {"small": {"n": 0, "rehit": 0, "spins": []},
                "medium": {"n": 0, "rehit": 0, "spins": []},
                "large": {"n": 0, "rehit": 0, "spins": []}}

        # We don't have explicit session IDs, so we use a pragmatic proxy:
        # count how often a second feature appears after a first feature of each size
        # by looking at the distribution of first mults that were followed by a feature_win_num==2
        # (This is approximate but works with the current log structure.)
        for _, row in first_hits.iterrows():
            b = _bucket(row["_mult"])
            post[b]["n"] += 1

        # Second hits that have a preceding first hit in the same "block"
        # Heuristic: for every second hit, look at the nearest preceding first hit mult
        second_rows = parsed[(parsed["_feature_win_num"] == 2) | ((parsed["_hit"] == 2) & (parsed["_attempt"] == 2))]
        for _, srow in second_rows.iterrows():
            # Find the most recent first hit before this row (by index order)
            prev = first_hits[first_hits.index < srow.name]
            if prev.empty:
                continue
            prev_mult = prev.iloc[-1]["_mult"]
            b = _bucket(prev_mult)
            post[b]["rehit"] += 1
            if pd.notna(srow["_spins"]):
                post[b]["spins"].append(float(srow["_spins"]))

        for b in post:
            n = post[b]["n"]
            r = post[b]["rehit"]
            spins = post[b]["spins"]
            post[b] = {
                "n_first": n,
                "n_rehit": r,
                "rehit_rate": round(r / n * 100, 1) if n > 0 else None,
                "median_spins_to_rehit": int(_safe_percentile(spins, 50)) if len(spins) >= 2 else (int(np.median(spins)) if spins else None),
                "p75_spins_to_rehit": int(_safe_percentile(spins, 75)) if len(spins) >= 2 else None,
            }
        profile["post_win"] = post
        profile["mult_buckets"] = {"q33": round(q33, 1), "q66": round(q66, 1)}
    else:
        profile["post_win"] = {}
        profile["mult_buckets"] = {}

    # Sample quality
    n1 = profile["hit_numbers"].get(1, {}).get("n_total", 0)
    if n1 >= 15:
        profile["sample_quality"] = "High"
    elif n1 >= 7:
        profile["sample_quality"] = "Medium"
    else:
        profile["sample_quality"] = "Low"

    return profile


def decide_next_action(family_name, slot_name, attempt_num, spins_so_far, last_mult, current_bet, live_df):
    """
    Pure decision function.
    Returns a dict with Action, Bet advice, Max spins left, Confidence, Reason.
    Everything is derived from the slot's own history.
    """
    profile = build_slot_behaviour_profile(family_name, slot_name, live_df)
    if not profile.get("ok"):
        return {
            "action": "UNKNOWN",
            "bet_advice": "Insufficient data – play conservatively or switch",
            "max_spins_left": None,
            "confidence": "None",
            "reason": profile.get("reason", "No history for this slot"),
            "profile": profile,
        }

    attempt_num = int(attempt_num) if attempt_num else 1
    spins_so_far = float(spins_so_far) if spins_so_far is not None else 0.0
    last_mult = float(last_mult) if last_mult is not None and last_mult != "" else None
    current_bet = float(current_bet) if current_bet else 5.0

    hn_info = profile["hit_numbers"].get(attempt_num, {})
    n_total = hn_info.get("n_total", 0)
    n_events = hn_info.get("n_events", 0)
    km85 = hn_info.get("km_p85")
    p75 = hn_info.get("p75")
    p85 = hn_info.get("p85")
    median = hn_info.get("median")

    # Choose the most reliable upper bound
    upper = km85 or p85 or p75 or median
    if upper is None:
        upper = 80  # absolute fallback only when zero data

    # How far into the distribution are we?
    # Approximate survival: % of historical attempts that lasted longer than spins_so_far
    event_spins = hn_info.get("event_spins", [])
    cens_spins = hn_info.get("censored_spins", [])
    still_alive = sum(1 for t in event_spins + cens_spins if t > spins_so_far)
    total_obs = len(event_spins) + len(cens_spins)
    pct_still_going = (still_alive / total_obs * 100) if total_obs > 0 else 50.0

    # ----- Decision logic (all thresholds derived from this slot) -----
    action = "STAY"
    bet_advice = f"Stay at ${current_bet:.2f}"
    max_left = max(0, int(upper - spins_so_far))
    confidence = profile["sample_quality"]
    reasons = []

    # 1. Already past the slot's own upper tail → WALK
    if spins_so_far >= upper and n_total >= 3:
        action = "WALK"
        bet_advice = "Walk – you are past this slot's historical upper range"
        max_left = 0
        reasons.append(f"Spins so far ({int(spins_so_far)}) ≥ slot's own 85th-percentile / KM estimate ({upper}).")
    elif pct_still_going < 15 and n_total >= 5:
        action = "WALK"
        bet_advice = "Walk – very few historical attempts lasted this long"
        max_left = 0
        reasons.append(f"Only {pct_still_going:.0f}% of past attempts of this hit number lasted beyond {int(spins_so_far)} spins.")

    # 2. Just hit a feature – decide whether to Judo Jump, Stay, or Walk for the NEXT attempt
    elif last_mult is not None and last_mult > 0 and attempt_num >= 1:
        post = profile.get("post_win", {})
        buckets = profile.get("mult_buckets", {})
        q33 = buckets.get("q33")
        q66 = buckets.get("q66")

        if q33 is not None and q66 is not None:
            if last_mult <= q33:
                bucket = "small"
            elif last_mult <= q66:
                bucket = "medium"
            else:
                bucket = "large"
        else:
            # Fallback buckets if not enough data
            if last_mult <= 35:
                bucket = "small"
            elif last_mult <= 70:
                bucket = "medium"
            else:
                bucket = "large"

        bstats = post.get(bucket, {})
        rehit_rate = bstats.get("rehit_rate")
        med_rehit = bstats.get("median_spins_to_rehit")
        p75_rehit = bstats.get("p75_spins_to_rehit")

        overall_rate = profile.get("overall_multi_hit_rate", 0)

        if rehit_rate is not None and bstats.get("n_first", 0) >= 2:
            if rehit_rate >= 45 and (med_rehit is not None and med_rehit <= 40):
                action = "JUDO JUMP"
                # Suggest a modest raise – player can choose exact size
                suggested = min(current_bet * 1.5, current_bet + 5) if current_bet < 10 else current_bet * 1.25
                suggested = round(suggested * 2) / 2  # neat 0.5 steps
                bet_advice = f"Judo Jump – raise toward ${suggested:.2f} for next ~{p75_rehit or med_rehit or 30} spins"
                max_left = p75_rehit or med_rehit or 35
                reasons.append(
                    f"After a {bucket} win (≤{q66 if bucket!='large' else 'top'}× on this slot) the machine re-hit "
                    f"{rehit_rate}% of the time, usually inside {med_rehit} spins."
                )
            elif rehit_rate < 25 and bucket == "large":
                action = "WALK"
                bet_advice = "Walk or drop significantly – large wins on this slot historically cool it"
                max_left = 0
                reasons.append(
                    f"After large wins this slot only re-hit {rehit_rate}% of the time. "
                    f"Overall multi-hit rate is {overall_rate}%."
                )
            else:
                action = "STAY"
                bet_advice = f"Stay at ${current_bet:.2f}"
                max_left = p75_rehit or med_rehit or (profile["hit_numbers"].get(attempt_num + 1, {}).get("p75") or 40)
                reasons.append(
                    f"After {bucket} wins the re-hit rate is {rehit_rate}%. "
                    f"Continuing at same bet for ~{max_left} spins is reasonable."
                )
        else:
            # Not enough conditional data – fall back to overall multi-hit rate
            if overall_rate >= 40:
                action = "STAY"
                bet_advice = f"Stay at ${current_bet:.2f} (overall multi-hit rate {overall_rate}%)"
                max_left = profile["hit_numbers"].get(min(attempt_num + 1, 4), {}).get("p75") or 40
                reasons.append(f"Overall multi-hit rate is solid ({overall_rate}%). Conditional data still thin.")
            else:
                action = "STAY"
                bet_advice = f"Stay or consider a smaller next bet (multi-hit rate only {overall_rate}%)"
                max_left = 30
                reasons.append(f"Limited conditional data and overall multi-hit rate is {overall_rate}%.")

    # 3. Still hunting for the current hit – normal continue / caution
    else:
        if spins_so_far < (median or 25):
            action = "STAY"
            bet_advice = f"Stay at ${current_bet:.2f} – still inside the typical window"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"Median for this hit number is ~{median}. You are at {int(spins_so_far)}.")
        elif spins_so_far < (p75 or upper):
            action = "STAY"
            bet_advice = f"Stay at ${current_bet:.2f} – approaching upper range, stay alert"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"You are between median and 75th percentile. Upper estimate ≈ {upper}.")
        else:
            action = "STAY"
            bet_advice = f"Caution – nearing historical limit. Max remaining ≈ {max(0, int(upper - spins_so_far))}"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"You are past the 75th percentile. Few attempts go much beyond {upper}.")

    # Prefer $5+ bets – soft note only
    if current_bet < 5.0 and action in ("STAY", "JUDO JUMP"):
        reasons.append("Note: you generally avoid bets below $5.")

    reason_text = " ".join(reasons) if reasons else "Based on this slot’s own history."

    return {
        "action": action,
        "bet_advice": bet_advice,
        "max_spins_left": max_left,
        "confidence": confidence,
        "reason": reason_text,
        "profile": profile,
        "upper_bound_used": upper,
        "pct_still_going": round(pct_still_going, 1),
    }

# ==========================================
# 2B. GAMBLE DATA ENGINE  (REPLACED – Variable-Order Markov)
# ==========================================
@st.cache_data(ttl=10)
def load_gamble_data():
    try:
        df = conn.read(worksheet=GAMBLE_WORKSHEET, ttl="0")
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()

def append_gamble_record(record: dict):
    try:
        existing = load_gamble_data()
        new_row = pd.DataFrame([record])
        if not existing.empty:
            for col in existing.columns:
                if col not in new_row.columns:
                    new_row[col] = ""
            for col in new_row.columns:
                if col not in existing.columns:
                    existing[col] = ""
            updated = pd.concat([existing.astype(str), new_row.astype(str)], ignore_index=True)
        else:
            updated = new_row.astype(str)
        conn.update(worksheet=GAMBLE_WORKSHEET, data=updated)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to write Gamble Log: {e}")
        return False

def delete_gamble_records(timestamps_to_delete: list):
    """Delete specific rows from the Gamble Log by Timestamp and update the Google Sheet."""
    try:
        existing = load_gamble_data()
        if existing.empty or "Timestamp" not in existing.columns:
            return False
        ts_set = set(str(t).strip() for t in timestamps_to_delete)
        mask = ~existing["Timestamp"].astype(str).str.strip().isin(ts_set)
        updated = existing[mask].reset_index(drop=True)
        conn.update(worksheet=GAMBLE_WORKSHEET, data=updated)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to delete from Gamble Log: {e}")
        return False

def _parse_sequence_str(seq_str: str) -> list:
    if not seq_str or not isinstance(seq_str, str):
        return []
    parts = [p.strip() for p in seq_str.split("-") if p.strip()]
    return [p for p in parts if p in SUITS]

def _build_extended_sequence(current_seq: list, recent_df: pd.DataFrame) -> list:
    if recent_df.empty or "Sequence" not in recent_df.columns or len(current_seq) < 4:
        return current_seq[:]
    extended = current_seq[:]
    for _, row in recent_df.iloc[::-1].iterrows():
        prev = _parse_sequence_str(str(row.get("Sequence", "")))
        if len(prev) < 5:
            continue
        if extended[:4] == prev[-4:]:
            extended = prev[:-4] + extended
        else:
            break
    return extended[-12:] if len(extended) > 12 else extended


# ---------------------------------------------------------------------------
# VARIABLE-ORDER MARKOV ENGINE
# Tries longest context first (order 5 → 4 → 3 → 2 → 1 → 0).
# Uses a context only when it has been observed at least MIN_N times.
# This is the algorithm that achieved ~62% in-sample suit accuracy on the log.
# ---------------------------------------------------------------------------
MIN_N_BY_ORDER = {5: 2, 4: 2, 3: 2, 2: 3, 1: 4, 0: 1}

def _build_markov_model(df: pd.DataFrame) -> dict:
    """Build frequency tables for every order 0..5 from the full log."""
    model = {o: defaultdict(Counter) for o in range(0, 6)}
    if df is None or df.empty:
        return model
    for _, row in df.iterrows():
        cards = []
        for i in range(1, 6):
            c = str(row.get(f"Card{i}", "")).strip()
            if c in SUITS:
                cards.append(c)
        nxt = str(row.get("Actual_Next", "")).strip()
        if len(cards) < 5 or nxt not in SUITS:
            # fallback: try Sequence column
            seq = _parse_sequence_str(str(row.get("Sequence", "")))
            if len(seq) >= 5 and nxt in SUITS:
                cards = seq[-5:]
            else:
                continue
        for o in range(0, 6):
            key = tuple(cards[-o:]) if o > 0 else ()
            model[o][key][nxt] += 1
    return model

def _decide_from_counts(counts: Counter):
    """Return (top_suit, count_of_top, total, full_counter). Ties broken by highest count only."""
    if not counts:
        return "Hearts", 0, 0, Counter()
    top = counts.most_common(1)[0][0]
    k_top = counts[top]
    total = sum(counts.values())
    return top, k_top, total, counts

def _grade(n_seen: int, k_top: int, order: int) -> str:
    if n_seen == 0:
        return "None"
    share = k_top / n_seen
    if order >= 4 and n_seen >= 3 and share >= 0.70:
        return "Strong"
    if order >= 3 and n_seen >= 4 and share >= 0.60:
        return "Strong"
    if n_seen >= 5 and share >= 0.55:
        return "Moderate"
    if n_seen >= 3 and share >= 0.45:
        return "Moderate"
    return "Weak"

def _suggest_core(sequence: list, df: pd.DataFrame):
    """
    Variable-order Markov suggestion.
    Tries longest matching context first (5-card → … → 1-card → overall base rate).
    Returns a dict compatible with the existing UI.
    """
    model = _build_markov_model(df)
    base_counts = model[0][()]
    if not base_counts:
        return {
            "color": "Red", "suit": "Hearts", "context_len": len(sequence),
            "match_count": 0, "confidence": "None", "note": "No data yet",
            "color_strength": 50.0, "matched": False, "outcomes": {},
            "match_len": 0,
        }

    base_suit, base_n, base_total, _ = _decide_from_counts(base_counts)

    if len(sequence) < 1:
        return {
            "color": SUIT_COLOR[base_suit], "suit": base_suit, "context_len": 0,
            "match_count": 0, "confidence": "None", "note": "Enter at least 1 card",
            "color_strength": 50.0, "matched": False, "outcomes": {},
            "match_len": 0,
        }

    # Longest context first
    max_try = min(5, len(sequence))
    for order in range(max_try, -1, -1):
        key = tuple(sequence[-order:]) if order > 0 else ()
        counts = model[order].get(key, Counter())
        total = sum(counts.values())
        min_needed = MIN_N_BY_ORDER.get(order, 2)
        if total < min_needed:
            continue

        top, k_top, n_seen, full_counts = _decide_from_counts(counts)
        color = SUIT_COLOR[top]
        color_share = sum(c for s, c in full_counts.items() if SUIT_COLOR[s] == color) / n_seen
        conf = _grade(n_seen, k_top, order)
        times = "time" if n_seen == 1 else "times"
        len_label = f"{order}-card" if order > 0 else "base-rate"

        return {
            "color": color,
            "suit": top,
            "context_len": order,
            "match_count": n_seen,
            "confidence": conf,
            "matched": order > 0,
            "outcomes": dict(full_counts),
            "match_len": order,
            "note": (
                f"{len_label} context — seen {n_seen} {times}; next was {top} "
                f"in {k_top}/{n_seen} cases ({round(100 * k_top / n_seen)}%)."
            ),
            "color_strength": round(color_share * 100, 1),
        }

    # Absolute fallback (should never reach here if base rate exists)
    return {
        "color": SUIT_COLOR[base_suit], "suit": base_suit, "context_len": 0,
        "match_count": 0, "confidence": "None", "matched": False, "outcomes": {},
        "match_len": 0,
        "note": f"Showing overall most common suit ({base_suit}).",
        "color_strength": 50.0,
    }

def get_gamble_suggestion(sequence: list, fade_color: bool = False):
    """
    Public API – Variable-Order Markov.
    If fade_color=True, invert the recommended colour (and pick the most common
    suit of the opposite colour from the same context counts when possible).
    This exists because live suit accuracy has been anti-predictive (~20%).
    """
    df = load_gamble_data()
    sug = _suggest_core(sequence, df)
    if not fade_color:
        sug["faded"] = False
        return sug

    # Invert colour
    raw_color = sug.get("color") or "Red"
    faded_color = "Black" if raw_color == "Red" else "Red"
    # Prefer a suit of the faded colour that appeared in outcomes; else any of that colour
    outcomes = sug.get("outcomes") or {}
    opposite_suits = [s for s in SUITS if SUIT_COLOR[s] == faded_color]
    best_suit, best_n = opposite_suits[0], -1
    for s in opposite_suits:
        n = outcomes.get(s, 0)
        if n > best_n:
            best_suit, best_n = s, n
    sug = dict(sug)
    sug["color"] = faded_color
    sug["suit"] = best_suit
    sug["faded"] = True
    sug["raw_color_before_fade"] = raw_color
    note = sug.get("note", "")
    sug["note"] = f"FADED (opposite of model). Model said {raw_color}. " + note
    return sug


@st.cache_data(ttl=60)
def backtest_gamble_accuracy(window: int = 50):
    """
    Honest walk-forward backtest of the Variable-Order Markov engine.
    Each row is predicted using ONLY the rows that appeared before it.
    """
    df = load_gamble_data()
    if df is None or df.empty or "Actual_Next" not in df.columns:
        return None

    sequences = []
    for _, row in df.iterrows():
        cards = []
        for i in range(1, 6):
            c = str(row.get(f"Card{i}", "")).strip()
            if c in SUITS:
                cards.append(c)
        nxt = str(row.get("Actual_Next", "")).strip()
        if len(cards) == 5 and nxt in SUITS:
            sequences.append((cards, nxt))
        else:
            seq = _parse_sequence_str(str(row.get("Sequence", "")))
            if len(seq) >= 5 and nxt in SUITS:
                sequences.append((seq[-5:], nxt))

    warm_up = 40
    if len(sequences) < warm_up + 5:
        return None

    # Incremental indexes
    indexes = {o: defaultdict(list) for o in range(0, 6)}
    base = Counter()
    records = []

    for i, (cards5, actual) in enumerate(sequences):
        if i >= warm_up:
            pred = None
            matched = False
            grade = "None"
            match_len = 0
            for order in range(5, -1, -1):
                key = tuple(cards5[-order:]) if order > 0 else ()
                outs = indexes[order].get(key, [])
                min_needed = MIN_N_BY_ORDER.get(order, 2)
                if len(outs) >= min_needed:
                    counts = Counter(outs)
                    pred = counts.most_common(1)[0][0]
                    k_top = counts[pred]
                    matched = order > 0
                    grade = _grade(len(outs), k_top, order)
                    match_len = order
                    break
            if pred is None:
                pred = base.most_common(1)[0][0] if base else "Hearts"
            pred_color = SUIT_COLOR[pred]
            actual_color = SUIT_COLOR[actual]
            records.append({
                "color_correct": pred_color == actual_color,
                "fade_color_correct": pred_color != actual_color,  # opposite colour wins
                "suit_correct": pred == actual,
                "matched": matched,
                "grade": grade,
                "match_len": match_len,
            })

        # Update indexes with the current observation
        for order in range(0, 6):
            key = tuple(cards5[-order:]) if order > 0 else ()
            indexes[order][key].append(actual)
        base[actual] += 1

    bt = pd.DataFrame(records)
    recent = bt.tail(window)

    def acc(frame, col):
        return round(frame[col].mean() * 100, 1) if len(frame) else None

    matched_df = bt[bt["matched"]]
    unmatched_df = bt[~bt["matched"]]
    strong_df = bt[bt["grade"] == "Strong"]
    by_len = {}
    for n in (5, 4, 3, 2, 1):
        sub = bt[bt["match_len"] == n]
        by_len[n] = {"n": len(sub), "suit_acc": acc(sub, "suit_correct")}

    return {
        "n_total": len(bt),
        "n_recent": len(recent),
        "overall_color_acc": acc(bt, "color_correct"),
        "overall_suit_acc": acc(bt, "suit_correct"),
        "overall_fade_color_acc": acc(bt, "fade_color_correct"),
        "recent_color_acc": acc(recent, "color_correct"),
        "recent_suit_acc": acc(recent, "suit_correct"),
        "recent_fade_color_acc": acc(recent, "fade_color_correct"),
        "n_matched": len(matched_df),
        "matched_suit_acc": acc(matched_df, "suit_correct"),
        "matched_color_acc": acc(matched_df, "color_correct"),
        "n_unmatched": len(unmatched_df),
        "unmatched_suit_acc": acc(unmatched_df, "suit_correct"),
        "n_strong": len(strong_df),
        "strong_suit_acc": acc(strong_df, "suit_correct"),
        "baseline_color_acc": 50.0,
        "baseline_suit_acc": 25.0,
        "by_len": by_len,
    }

# ==========================================
# 3. AI AGENT ENGINE  (Gemini → Groq fallback)
# ==========================================
@st.cache_resource
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

@st.cache_resource
def get_groq_client():
    if Groq is None:
        return None
    api_key = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", None)
    if not api_key:
        return None
    return Groq(api_key=api_key)

def tool_mark_machine_played(slot_name: str) -> str:
    return mark_slot_played(slot_name)

def tool_update_bankroll(new_amount: float) -> str:
    st.session_state.current_bankroll = float(new_amount)
    persist_session_state()
    return f"Current bankroll updated to ${new_amount:.2f}"

AVAILABLE_TOOLS = {
    "tool_mark_machine_played": tool_mark_machine_played,
    "tool_update_bankroll": tool_update_bankroll,
}

def build_agent_context():
    available_slots = [
        s for s in st.session_state.slots_db
        if s["slot"] not in st.session_state.played_basket
        and (s.get("rehit_metrics", {}).get("first_hit_total", 0)) > 5
    ]
    slot_context_summary = []
    for s in available_slots[:20]:
        slot_context_summary.append({
            "slot": s["slot"], "family": s["family"], "rvi_score": s["base_rvi"],
            "multi_hit_rate": f"{s['rehit_metrics']['multi_hit_rate']}%",
            "multi_hit_count": s['rehit_metrics']['multi_hit_count'],
            "attempt2_population": s['rehit_metrics'].get('attempt2_population', 0),
            "strategy_plan": STRATEGY_PLAN_SUMMARY, "checkin_alloc": "$500",
            "recommendation_protocol": s['rehit_metrics']['repeat_recommendation']
        })
    system_instruction = f"""
    You are an expert AI Casino Slot Optimization & Execution Agent.
    CURRENT LIVE SESSION ENVIRONMENT:
    - Active Target Day: {st.session_state.selected_day}
    - Current Active Bankroll: ${st.session_state.current_bankroll:.2f}
    - Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}
    - Target Bankroll: ${st.session_state.session_target:.2f}
    - Played Basket (Played Today): {st.session_state.played_basket}
    EXECUTION STRATEGY IN USE:
    - Check-in: $500 per machine across 5 denoms ($100 budget per denom).
    - Fixed Bet Denom Rotation: Always $5.00 bet per spin.
    - Exit Criteria: Book profit at $700+ or exit if back to $500.
    AVAILABLE TOP-RANKED SLOTS DATASET:
    {slot_context_summary}
    """
    return system_instruction

def run_gemini_agent(user_prompt: str):
    client = get_gemini_client()
    if not client:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    system_instruction = build_agent_context()
    contents = []
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))
    tools_list = [tool_mark_machine_played, tool_update_bankroll]
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools_list,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
    state_changed = False
    if response.function_calls:
        contents.append(response.candidates[0].content)
        function_response_parts = []
        for fn in response.function_calls:
            handler = AVAILABLE_TOOLS.get(fn.name)
            tool_result = handler(**(dict(fn.args) if fn.args else {})) if handler else f"Unknown tool '{fn.name}'."
            state_changed = True
            function_response_parts.append(
                types.Part.from_function_response(name=fn.name, response={"result": tool_result})
            )
        contents.append(types.Content(role="user", parts=function_response_parts))
        follow_up = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        return follow_up.text or "🤖 Action completed.", state_changed
    return response.text, state_changed

def run_groq_agent(user_prompt: str):
    client = get_groq_client()
    if not client:
        return "⚠️ Groq fallback unavailable."
    system_instruction = build_agent_context() + "\n\nNOTE: Text-only fallback mode."
    messages = [{"role": "system", "content": system_instruction}]
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_prompt})
    completion = client.chat.completions.create(model=GROQ_MODEL, messages=messages, temperature=0.3)
    return completion.choices[0].message.content

def run_ai_agent(user_prompt: str):
    try:
        text, state_changed = run_gemini_agent(user_prompt)
        if state_changed:
            st.session_state.pending_rerun = True
        return text, "Gemini"
    except Exception as gemini_err:
        try:
            text = run_groq_agent(user_prompt)
            return f"{text}\n\n_(⚠️ Gemini fallback via Groq: {gemini_err})_", "Groq (fallback)"
        except Exception as groq_err:
            return f"⚠️ AI providers failed:\n- Gemini: {gemini_err}\n- Groq: {groq_err}", "None"

def parse_ai_gamble_response(text: str) -> dict:
    """Extract Colour and Suit from AI free-text response. Returns dict with color, suit, reason, ok."""
    if not text:
        return {"ok": False, "color": None, "suit": None, "reason": ""}
    color, suit, reason = None, None, ""
    # Colour / Color
    m = re.search(r"(?i)\bcolou?r\s*[:\-]\s*(red|black)\b", text)
    if m:
        color = m.group(1).title()
    # Suit
    m = re.search(r"(?i)\bsuit\s*[:\-]\s*(hearts|diamonds|clubs|spades)\b", text)
    if m:
        suit = m.group(1).title()
    # Fallback: look for bare suit words near end
    if not suit:
        for s in SUITS:
            if re.search(rf"(?i)\b{s}\b", text):
                suit = s
                break
    if suit and not color:
        color = SUIT_COLOR.get(suit)
    # Reason
    m = re.search(r"(?i)\breason\s*[:\-]\s*(.+)", text)
    if m:
        reason = m.group(1).strip().split("\n")[0][:200]
    ok = suit in SUITS and color in ("Red", "Black")
    return {"ok": ok, "color": color, "suit": suit, "reason": reason, "raw": text}


def get_ai_gamble_suggestion(sequence: list, extended: list):
    """Ask AI for a gamble suggestion. Tries Gemini first, falls back to Groq on any error.
    Returns (raw_text, provider, parsed_dict).
    """
    prompt = f"""
You are helping with a casino gamble feature (colour/suit prediction).

Current 5-card sequence: {' → '.join(sequence)}
Extended recent chain: {' → '.join(extended) if extended else 'N/A'}

Based on historical patterns, what is the most likely next card colour (Red/Black) and suit (Hearts/Diamonds/Clubs/Spades)?
Remember: Hearts & Diamonds = Red, Clubs & Spades = Black.

Reply in this exact format:
Colour: Red or Black
Suit: Hearts / Diamonds / Clubs / Spades
Reason: short explanation
"""

    text, provider = None, "None"
    # 1. Try Gemini
    try:
        client = get_gemini_client()
        if client:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            text = response.text or "No response"
            provider = "Gemini"
    except Exception as e:
        gemini_error = str(e)
        text = None
    else:
        gemini_error = "No Gemini client"

    # 2. Fallback to Groq
    if text is None:
        try:
            client = get_groq_client()
            if client:
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                )
                text = completion.choices[0].message.content
                provider = "Groq (fallback)"
        except Exception as e:
            text = f"AI error (both providers failed):\nGemini: {gemini_error}\nGroq: {e}"
            provider = "None"

    if text is None:
        text = "AI unavailable (no API keys configured)."
        provider = "None"

    parsed = parse_ai_gamble_response(text)
    return text, provider, parsed


def compute_ai_vs_stat_performance(window: int = 50):
    """
    From Gamble Log rows that have a Source column (or inferred),
    compute suit/colour accuracy for Statistical vs AI suggestions.
    """
    df = load_gamble_data()
    if df is None or df.empty or "Actual_Next" not in df.columns:
        return None

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # Normalise optional columns
    if "Source" not in df.columns:
        df["Source"] = "Statistical"  # legacy rows treated as statistical
    else:
        df["Source"] = df["Source"].fillna("Statistical").astype(str).str.strip()

    def _acc(sub):
        if sub.empty:
            return None, None, 0
        suit_ok = (sub["Suggested_Suit"].astype(str).str.strip().str.title() ==
                   sub["Actual_Next"].astype(str).str.strip().str.title())
        # colour from suit if needed
        def _col(s):
            s = str(s).strip().title()
            return SUIT_COLOR.get(s, "")
        color_ok = sub["Suggested_Suit"].map(_col) == sub["Actual_Next"].map(_col)
        n = len(sub)
        return (
            round(float(suit_ok.mean()) * 100, 1) if n else None,
            round(float(color_ok.mean()) * 100, 1) if n else None,
            n,
        )

    stat = df[df["Source"].str.lower().isin(["statistical", "stat", "markov", ""])]
    ai = df[df["Source"].str.lower().isin(["ai", "gemini", "groq"])]

    # Prefer most recent window
    stat_recent = stat.tail(window)
    ai_recent = ai.tail(window)

    s_suit, s_col, s_n = _acc(stat)
    s_suit_r, s_col_r, s_n_r = _acc(stat_recent)
    a_suit, a_col, a_n = _acc(ai)
    a_suit_r, a_col_r, a_n_r = _acc(ai_recent)

    return {
        "stat_suit_all": s_suit, "stat_color_all": s_col, "stat_n_all": s_n,
        "stat_suit_recent": s_suit_r, "stat_color_recent": s_col_r, "stat_n_recent": s_n_r,
        "ai_suit_all": a_suit, "ai_color_all": a_col, "ai_n_all": a_n,
        "ai_suit_recent": a_suit_r, "ai_color_recent": a_col_r, "ai_n_recent": a_n_r,
        "window": window,
    }

def get_ai_priority_ranking(slots_db, selected_day, played_basket):
    """Ask AI to re-rank machines. Tries Gemini first, falls back to Groq."""
    candidates = []
    for s in slots_db:
        if s["slot"] in played_basket:
            continue
        rehit = s.get("rehit_metrics", {})
        if rehit.get("first_hit_total", 0) < 5:
            continue
        candidates.append({
            "slot": s["slot"],
            "family": s["family"],
            "composite": s.get("composite", 0),
            "avg_mult": rehit.get("avg_first_multiplier", 0),
            "hit_rate": f"{rehit.get('first_hit_count',0)}/{rehit.get('first_hit_total',0)}",
            "spin_1st": s.get("spin_1st"),
            "multi_rate": rehit.get("multi_hit_rate", 0)
        })
    
    candidates = candidates[:60]
    
    prompt = f"""
You are an expert slot machine ranking engine for live casino play.

Target day: {selected_day}
Already played today: {played_basket}

Here are the current statistical candidates (top 60):
{candidates}

Re-rank the best 40-50 machines for today, prioritising:
1. High average multipliers
2. Good hit rates with decent sample size
3. Machines that have shown strong repeat potential
4. Avoid pure grinders that rarely pay big

Return ONLY a clean numbered list in this exact format (one per line):
1. Family | Slot Name
2. Family | Slot Name
...
Do not add extra text before or after the list.
"""

    # 1. Try Gemini
    try:
        client = get_gemini_client()
        if client:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            return response.text or "", "Gemini"
    except Exception as e:
        gemini_error = str(e)
    else:
        gemini_error = "No Gemini client"

    # 2. Fallback to Groq
    try:
        client = get_groq_client()
        if client:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            text = completion.choices[0].message.content
            return text, "Groq (fallback)"
    except Exception as e:
        return None, f"Both failed – Gemini: {gemini_error} | Groq: {e}"

    return None, "No AI providers available"

def parse_ai_priority_list(ai_text: str, slots_db: list):
    """Parse the AI numbered list back into slot records."""
    if not ai_text:
        return []
    
    lines = [l.strip() for l in ai_text.strip().splitlines() if l.strip()]
    parsed = []
    slot_lookup = {(s["family"].lower(), s["slot"].lower()): s for s in slots_db}
    
    for line in lines:
        match = re.match(r"^\d+[\.\)]\s*(.+?)\s*\|\s*(.+)$", line)
        if not match:
            continue
        fam = match.group(1).strip()
        slot = match.group(2).strip()
        
        key = (fam.lower(), slot.lower())
        if key in slot_lookup:
            parsed.append(slot_lookup[key])
        else:
            for s in slots_db:
                if s["slot"].lower() == slot.lower():
                    parsed.append(s)
                    break
    return parsed

# ==========================================
# LOAD DATA & INITIALIZE STATE
# ==========================================
SLOTS_DB_VERSION = 4  # bump when priority schema / ranking weights change
live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()
if (
    "slots_db" not in st.session_state
    or not st.session_state.slots_db
    or st.session_state.get("slots_db_version") != SLOTS_DB_VERSION
):
    st.session_state.slots_db = build_priority_dataset(
        live_sheet_df,
        st.session_state.selected_day,
        st.session_state.strict_day_penalty
    )
    st.session_state.slots_db_version = SLOTS_DB_VERSION

# ==========================================
# 4. SIDEBAR & NAVIGATION
# ==========================================
st.sidebar.title("🎰 Live Session Hub")
if st.session_state.get("session_was_restored"):
    st.sidebar.info("♻️ Restored active session data.")
if st.sidebar.button("🔄 Reset All Session Data", use_container_width=True, type="primary"):
    reset_all_state()
    st.rerun()

st.sidebar.markdown("---")
if detected_sheet_cols:
    st.sidebar.success(f"🟢 GSheet Connected ({len(detected_sheet_cols)} Cols)")
else:
    st.sidebar.warning("🟡 GSheet Off-line")

st.sidebar.subheader("📅 Day-of-Week Focus")
days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
current_day_idx = datetime.now().weekday()
default_day_idx = days_list.index(st.session_state.selected_day) if st.session_state.selected_day in days_list else current_day_idx
selected_day_input = st.sidebar.selectbox("Filter Target Day:", options=days_list, index=default_day_idx)
strict_penalty_toggle = st.sidebar.checkbox("Strict Day Match (Penalize 0-Hit Days)", value=st.session_state.strict_day_penalty)
if selected_day_input != st.session_state.selected_day or strict_penalty_toggle != st.session_state.strict_day_penalty:
    st.session_state.selected_day = selected_day_input
    st.session_state.strict_day_penalty = strict_penalty_toggle
    st.session_state.slots_db = build_priority_dataset(live_sheet_df, st.session_state.selected_day, st.session_state.strict_day_penalty)
    persist_session_state()
    st.rerun()

st.sidebar.subheader("📌 Navigation")
for tab_name in TAB_OPTIONS:
    is_active = (st.session_state.active_tab == tab_name)
    btn_type = "primary" if is_active else "secondary"
    if st.sidebar.button(tab_name, key=f"nav_btn_{tab_name}", use_container_width=True, type=btn_type):
        st.session_state.active_tab = tab_name
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Bankroll & Profit Lock")
with st.sidebar.form("bankroll_form"):
    new_start = st.number_input("Starting Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
    new_current = st.number_input("Current Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
    new_target = st.number_input("Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)
    new_stop_win = st.number_input("Stop-Win / Lock at +($)", value=float(st.session_state.stop_win), step=50.0)
    new_stop_loss = st.number_input("Stop-Loss at −($)", value=float(st.session_state.stop_loss), step=50.0)
    bankroll_submit = st.form_submit_button("💾 Update & Save")
    if bankroll_submit:
        st.session_state.session_start_bankroll = new_start
        st.session_state.current_bankroll = new_current
        st.session_state.session_target = new_target
        st.session_state.stop_win = new_stop_win
        st.session_state.stop_loss = new_stop_loss
        persist_session_state()
        st.rerun()

_sp = session_profit_status()
st.sidebar.metric("Locked P&L", f"${_sp['pnl']:+.0f}")
if _sp["status"] in ("STOP_LOSS",):
    st.sidebar.error(_sp["message"])
elif _sp["status"] in ("LOCK_PROFIT", "TARGET_HIT"):
    st.sidebar.success(_sp["message"])
elif _sp["status"] == "AHEAD":
    st.sidebar.info(_sp["message"])
else:
    st.sidebar.warning(_sp["message"])

st.sidebar.markdown("---")
st.sidebar.subheader("🃏 Gamble mode")
st.session_state.fade_gamble = st.sidebar.checkbox(
    "Fade statistical colour (recommend opposite)",
    value=bool(st.session_state.fade_gamble),
    help="Turn ON when the model is anti-predictive. You have been winning by taking the opposite colour.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("✅ Quick Mark Played")
qm_family = st.sidebar.selectbox("Family:", options=list(SLOT_MASTER_LIST.keys()), key="qm_fam")
qm_slot = st.sidebar.selectbox("Slot:", options=SLOT_MASTER_LIST[qm_family], key="qm_slot")
if st.sidebar.button("Mark as Played", use_container_width=True):
    res = mark_slot_played(qm_slot)
    st.sidebar.success(res)
    st.rerun()

# ==========================================
# 5. DASHBOARD VIEWS
# ==========================================

# Persistent session banner (plain HTML — labels always visible, no Streamlit metric glitch)
_sp = session_profit_status()
_pnl_txt = f"+${_sp['pnl']:.0f}" if _sp['pnl'] >= 0 else f"-${abs(_sp['pnl']):.0f}"
st.markdown(
    f"""<div class="session-banner">
    <b>Bankroll:</b> ${_sp['current']:.0f}
    &nbsp;·&nbsp; <b>Session P&amp;L:</b> {_pnl_txt}
    &nbsp;·&nbsp; <b>Start:</b> ${_sp['start']:.0f}
    &nbsp;·&nbsp; <b>Profit-lock at:</b> +${_sp['stop_win']:.0f}
    &nbsp;·&nbsp; <b>Stop-loss at:</b> −${_sp['stop_loss']:.0f}
    </div>""",
    unsafe_allow_html=True,
)
if _sp["status"] == "STOP_LOSS":
    st.error(f"🛑 {_sp['message']}")
elif _sp["status"] in ("LOCK_PROFIT", "TARGET_HIT"):
    st.success(f"🔒 {_sp['message']}")
elif _sp["status"] == "AHEAD":
    st.info(f"✅ {_sp['message']}")
else:
    st.warning(f"📉 {_sp['message']}")

if st.session_state.active_tab == "🎯 Live Decision":
    st.subheader("🎯 Live Decision Engine")
    st.caption(
        "Fully data-driven per-slot advice. Uses right-censored walk-offs (+), "
        "Kaplan-Meier style percentiles, and post-win behaviour unique to each machine."
    )

    # Build family → slots map from master list + any extra seen in data
    all_families = sorted(SLOT_MASTER_LIST.keys())
    col_a, col_b = st.columns(2)
    with col_a:
        sel_family = st.selectbox("Family", options=all_families, key="ld_family")
    with col_b:
        slot_opts = SLOT_MASTER_LIST.get(sel_family, [])
        sel_slot = st.selectbox("Slot", options=slot_opts, key="ld_slot")

    st.markdown("#### Current situation at the machine")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        attempt_in = st.number_input("Attempt # (1 = first feature hunt)", min_value=1, max_value=15, value=1, step=1, key="ld_attempt")
    with c2:
        spins_in = st.number_input("Spins already played this attempt", min_value=0, max_value=500, value=0, step=1, key="ld_spins")
    with c3:
        last_mult_in = st.number_input("Last feature multiplier (0 if none yet)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0, key="ld_mult")
    with c4:
        bet_in = st.number_input("Current bet ($)", min_value=0.5, max_value=50.0, value=5.0, step=0.5, key="ld_bet")

    if st.button("Get Recommendation", type="primary", use_container_width=True, key="ld_run"):
        with st.spinner("Building per-slot behaviour profile from history…"):
            result = decide_next_action(
                family_name=sel_family,
                slot_name=sel_slot,
                attempt_num=attempt_in,
                spins_so_far=spins_in,
                last_mult=last_mult_in if last_mult_in > 0 else None,
                current_bet=bet_in,
                live_df=live_sheet_df,
            )

        st.markdown("---")
        action = result["action"]
        color_map = {
            "STAY": "blue",
            "JUDO JUMP": "green",
            "WALK": "red",
            "DROP BET": "orange",
            "UNKNOWN": "gray",
        }
        st.markdown(f"### Recommendation: :{color_map.get(action, 'gray')}[{action}]")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Action", action)
        m2.metric("Max spins left", result["max_spins_left"] if result["max_spins_left"] is not None else "—")
        m3.metric("Confidence", result["confidence"])
        m4.metric("Upper bound used", result.get("upper_bound_used", "—"))

        st.info(f"**Bet advice:** {result['bet_advice']}")
        st.write(f"**Reason:** {result['reason']}")

        # Session profit-lock overlay on machine decisions
        _sp2 = session_profit_status()
        if _sp2["status"] == "STOP_LOSS":
            st.error("Session stop-loss is active — do not start a new machine.")
        elif _sp2["status"] in ("LOCK_PROFIT", "TARGET_HIT"):
            st.warning("Profit-lock territory. Continue only on A-tier machines with reduced check-in.")
        elif _sp2["status"] == "AHEAD" and result["action"] == "JUDO JUMP":
            st.info("Already ahead — JJ only with a tight spin budget; protect the gain.")

        # Show the underlying profile so the player can trust / override
        with st.expander("🔍 Slot behaviour profile (what the engine actually saw)", expanded=False):
            prof = result.get("profile", {})
            if not prof.get("ok"):
                st.write(prof.get("reason", "No profile"))
            else:
                st.write(f"**Sample quality:** {prof.get('sample_quality')}  |  **Total logs:** {prof.get('total_logs')}  |  "
                         f"**Overall multi-hit rate:** {prof.get('overall_multi_hit_rate')}%  |  "
                         f"**Clustering score:** {prof.get('clustering_score')} (0=regular, 1=extreme clusters)")

                st.markdown("**Spin distributions by hit number**")
                rows = []
                for hn, info in sorted(prof.get("hit_numbers", {}).items()):
                    rows.append({
                        "Hit #": hn,
                        "Events": info.get("n_events"),
                        "Walk-offs (+)": info.get("n_censored"),
                        "Median": info.get("median"),
                        "P75": info.get("p75"),
                        "P85 (obs)": info.get("p85"),
                        "KM P85": info.get("km_p85"),
                        "Avg Mult": info.get("avg_mult"),
                        "Max Mult": info.get("max_mult"),
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                post = prof.get("post_win", {})
                if post:
                    st.markdown("**Post-win behaviour (JJ intelligence)**")
                    buckets = prof.get("mult_buckets", {})
                    st.caption(f"Small ≤ {buckets.get('q33')}×   |   Medium ≤ {buckets.get('q66')}×   |   Large > {buckets.get('q66')}×")
                    post_rows = []
                    for b in ["small", "medium", "large"]:
                        binfo = post.get(b, {})
                        post_rows.append({
                            "Win size": b.title(),
                            "First hits seen": binfo.get("n_first"),
                            "Re-hits": binfo.get("n_rehit"),
                            "Re-hit rate": f"{binfo.get('rehit_rate')}%" if binfo.get("rehit_rate") is not None else "—",
                            "Median spins to re-hit": binfo.get("median_spins_to_rehit"),
                            "P75 spins to re-hit": binfo.get("p75_spins_to_rehit"),
                        })
                    st.dataframe(pd.DataFrame(post_rows), use_container_width=True, hide_index=True)

                st.caption(
                    f"% of historical attempts still going after {int(spins_in)} spins: "
                    f"{result.get('pct_still_going', '—')}%"
                )

    else:
        st.markdown("---")
        st.info("Enter the current situation and press **Get Recommendation**.")

elif st.session_state.active_tab == "🃏 Gamble Analyzer":
    st.subheader("🃏 Gamble Analyzer")
    st.caption("Statistical (Markov) + AI suggestions side by side. Log either with one tap. Track both accuracies.")

    def _fmt_pct(v):
        return "n/a" if v is None else f"{v}%"

    # ---- Performance: Statistical (walk-forward) + AI (logged Source) ----
    with st.expander("📉 Accuracy — Statistical vs AI", expanded=True):
        bt = backtest_gamble_accuracy(window=50)
        perf = compute_ai_vs_stat_performance(window=50)

        st.markdown("**Statistical engine** (honest walk-forward)")
        if bt is None:
            st.info("Need ~45+ logged rows for statistical backtest.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Colour (all)", _fmt_pct(bt.get("overall_color_acc")), "vs 50%")
            c2.metric("FADE colour (all)", _fmt_pct(bt.get("overall_fade_color_acc")), "opposite of model")
            c3.metric(f"FADE colour (last {bt['n_recent']})", _fmt_pct(bt.get("recent_fade_color_acc")))
            c4.metric("Suit (all)", _fmt_pct(bt["overall_suit_acc"]), "vs 25%")
            if (bt.get("overall_fade_color_acc") or 0) > (bt.get("overall_color_acc") or 0) + 5:
                st.warning(
                    "Model colour is anti-predictive on this log. **Fade mode is recommended** "
                    "(sidebar → Gamble mode). You bet the opposite colour."
                )

        st.markdown("**AI suggestions** (from rows you logged with Source = AI)")
        if perf is None or (perf.get("ai_n_all") or 0) == 0:
            st.info("No AI-logged results yet. Use the AI card’s ✅ Correct button to start tracking.")
        else:
            a1, a2, a3 = st.columns(3)
            a1.metric("Suit (all AI)", _fmt_pct(perf["ai_suit_all"]), f"n={perf['ai_n_all']}")
            a2.metric(f"Suit (last {perf['window']} AI)", _fmt_pct(perf["ai_suit_recent"]), f"n={perf['ai_n_recent']}")
            a3.metric("Colour (all AI)", _fmt_pct(perf["ai_color_all"]))
            if perf.get("stat_n_all"):
                st.caption(
                    f"Head-to-head on logged rows — Stat suit { _fmt_pct(perf['stat_suit_all']) } "
                    f"(n={perf['stat_n_all']}) vs AI suit { _fmt_pct(perf['ai_suit_all']) } (n={perf['ai_n_all']})."
                )

    # ---- Card entry ----
    st.markdown("### Enter the 5 cards")
    cols = st.columns(4)
    for i, suit in enumerate(SUITS):
        with cols[i]:
            if st.button(f"{SUIT_EMOJI[suit]} {suit}", key=f"suit_btn_{suit}", use_container_width=True):
                if len(st.session_state.gamble_sequence) < 5:
                    st.session_state.gamble_sequence.append(suit)
                    st.session_state.ai_gamble_suggestion = None
                    st.rerun()

    seq = st.session_state.gamble_sequence

    if seq:
        html_parts = [suit_html(s) for s in seq]
        st.markdown("**Sequence:** " + " → ".join(html_parts) + f" &nbsp;({len(seq)}/5)", unsafe_allow_html=True)
        if st.button("↺ Clear sequence", key="clear_seq", use_container_width=True):
            st.session_state.gamble_sequence = []
            st.session_state.ai_gamble_suggestion = None
            st.rerun()
    else:
        st.info("Tap the four suits above to build the sequence.")

    if len(seq) == 5:
        fade_on = bool(st.session_state.get("fade_gamble", True))
        sug = get_gamble_suggestion(seq, fade_color=fade_on)
        df_full = load_gamble_data()
        recent = df_full.tail(100) if len(df_full) > 100 else df_full
        extended = _build_extended_sequence(seq, recent)

        # ---- Statistical card (colour-first; optional fade) ----
        mode_label = "Statistical (FADED – bet opposite colour)" if sug.get("faded") else "Statistical suggestion"
        st.markdown(f"### {mode_label}")
        st.markdown('<div class="sug-card stat">', unsafe_allow_html=True)
        st.markdown(
            f"**Colour to play** &nbsp; {color_html(sug['color'])}<br>"
            f"**Suit (optional)** &nbsp; {suit_html(sug['suit'])}",
            unsafe_allow_html=True
        )
        if sug.get("faded"):
            st.caption(f"Raw model colour was **{sug.get('raw_color_before_fade')}** — faded because model has been anti-predictive.")
        conf = sug.get("confidence", "None")
        note = sug.get("note", "")
        match_len = sug.get("match_len", 0) or 0
        len_tag = f"{match_len}-card" if match_len else "base-rate"
        if conf == "Strong":
            st.success(f"**Strong {len_tag}** – {note}")
        elif conf == "Moderate":
            st.info(f"**Moderate {len_tag}** – {note}")
        elif conf == "Weak":
            st.warning(f"**Weak {len_tag}** – {note}")
        else:
            st.caption(f"No signal – {note}")
        if sug.get("outcomes"):
            breakdown = ", ".join(f"{s} ×{c}" for s, c in sorted(sug["outcomes"].items(), key=lambda x: -x[1]))
            st.caption(f"Seen {sug.get('match_count', 0)}× · Followed by: {breakdown}")
        st.markdown("</div>", unsafe_allow_html=True)

        if st.button("✅ Correct – Log Statistical", key="quick_correct", use_container_width=True, type="primary"):
            actual = sug["suit"]
            now = datetime.now()
            record = {
                "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "Date": now.strftime("%m/%d/%Y"),
                "Day": now.strftime("%A"),
                "Card1": seq[0], "Card2": seq[1], "Card3": seq[2], "Card4": seq[3], "Card5": seq[4],
                "Sequence": "-".join(seq),
                "Suggested_Color": sug["color"],
                "Suggested_Suit": sug["suit"],
                "Actual_Next": actual,
                "Actual_Color": SUIT_COLOR[actual],
                "Source": "Statistical",
            }
            if append_gamble_record(record):
                st.session_state.gamble_sequence = seq[1:] + [actual]
                st.session_state.ai_gamble_suggestion = None
                st.success("Logged Statistical as Correct. Sequence rolled forward.")
                st.rerun()

        # ---- AI card ----
        st.markdown("### AI suggestion")
        if st.button("🤖 Ask AI for suggestion", key="ask_ai_gamble", use_container_width=True):
            with st.spinner("AI analysing patterns…"):
                ai_text, provider, parsed = get_ai_gamble_suggestion(seq, extended)
                st.session_state.ai_gamble_suggestion = (ai_text, provider, parsed)
                st.rerun()

        if st.session_state.ai_gamble_suggestion:
            # Support old 2-tuple and new 3-tuple
            packed = st.session_state.ai_gamble_suggestion
            if len(packed) == 3:
                ai_text, provider, parsed = packed
            else:
                ai_text, provider = packed
                parsed = parse_ai_gamble_response(ai_text)

            st.markdown('<div class="sug-card ai">', unsafe_allow_html=True)
            if parsed.get("ok"):
                st.markdown(
                    f"**Colour** &nbsp; {color_html(parsed['color'])}<br>"
                    f"**Suit** &nbsp;&nbsp;&nbsp;&nbsp; {suit_html(parsed['suit'])}",
                    unsafe_allow_html=True
                )
                if parsed.get("reason"):
                    st.caption(parsed["reason"])
                st.caption(f"Source: {provider}")
            else:
                st.warning("Could not parse Colour/Suit from AI reply. Raw response below.")
                st.markdown(ai_text)
                st.caption(f"Source: {provider}")
            st.markdown("</div>", unsafe_allow_html=True)

            if parsed.get("ok"):
                if st.button("✅ Correct – Log AI", key="quick_correct_ai", use_container_width=True, type="primary"):
                    actual = parsed["suit"]
                    now = datetime.now()
                    record = {
                        "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "Date": now.strftime("%m/%d/%Y"),
                        "Day": now.strftime("%A"),
                        "Card1": seq[0], "Card2": seq[1], "Card3": seq[2], "Card4": seq[3], "Card5": seq[4],
                        "Sequence": "-".join(seq),
                        "Suggested_Color": parsed["color"],
                        "Suggested_Suit": parsed["suit"],
                        "Actual_Next": actual,
                        "Actual_Color": SUIT_COLOR[actual],
                        "Source": "AI",
                    }
                    if append_gamble_record(record):
                        st.session_state.gamble_sequence = seq[1:] + [actual]
                        st.session_state.ai_gamble_suggestion = None
                        st.success("Logged AI as Correct. Sequence rolled forward.")
                        st.rerun()

            with st.expander("Raw AI response"):
                st.markdown(ai_text)

        # ---- Manual log when both wrong ----
        st.markdown("### Both wrong? Log the real card")
        with st.form("log_gamble_result", clear_on_submit=False):
            actual = st.selectbox("Actual next card", options=SUITS, index=0, key="actual_select")
            # Which suggestion to attribute the miss to
            source_choice = st.radio(
                "Attribute this outcome to",
                options=["Statistical", "AI", "Both / Unknown"],
                horizontal=True,
                key="manual_source",
            )
            submitted = st.form_submit_button("💾 Log & roll forward", use_container_width=True)
            if submitted:
                now = datetime.now()
                # Prefer AI parsed suit as "suggested" if attributing to AI and we have it
                if source_choice == "AI" and st.session_state.ai_gamble_suggestion:
                    packed = st.session_state.ai_gamble_suggestion
                    parsed = packed[2] if len(packed) == 3 else parse_ai_gamble_response(packed[0])
                    sug_color = parsed.get("color") or sug["color"]
                    sug_suit = parsed.get("suit") or sug["suit"]
                    src = "AI"
                elif source_choice == "Statistical":
                    sug_color, sug_suit, src = sug["color"], sug["suit"], "Statistical"
                else:
                    sug_color, sug_suit, src = sug["color"], sug["suit"], "Statistical"

                record = {
                    "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "Date": now.strftime("%m/%d/%Y"),
                    "Day": now.strftime("%A"),
                    "Card1": seq[0], "Card2": seq[1], "Card3": seq[2], "Card4": seq[3], "Card5": seq[4],
                    "Sequence": "-".join(seq),
                    "Suggested_Color": sug_color,
                    "Suggested_Suit": sug_suit,
                    "Actual_Next": actual,
                    "Actual_Color": SUIT_COLOR[actual],
                    "Source": src,
                }
                if append_gamble_record(record):
                    st.session_state.gamble_sequence = seq[1:] + [actual]
                    st.session_state.ai_gamble_suggestion = None
                    st.success("Logged. Sequence rolled forward.")
                    st.rerun()

    # ---- Recent log ----
    st.markdown("---")
    st.markdown("### Recent log (last 12)")
    gdf = load_gamble_data()
    if not gdf.empty:
        show_cols = [c for c in ["Timestamp", "Sequence", "Suggested_Suit", "Actual_Next", "Source"] if c in gdf.columns]
        # ensure Source column visible even if missing historically
        if "Source" not in gdf.columns:
            gdf = gdf.copy()
            gdf["Source"] = "Statistical"
            show_cols = [c for c in ["Timestamp", "Sequence", "Suggested_Suit", "Actual_Next", "Source"] if c in gdf.columns]
        recent_df = gdf[show_cols].tail(12).iloc[::-1].reset_index(drop=True)

        st.caption("Select mistakes to delete. Deletion updates the Google Sheet.")

        selected_timestamps = []
        for idx, row in recent_df.iterrows():
            ts = str(row.get("Timestamp", "")).strip()
            seq_str = str(row.get("Sequence", ""))
            sug_suit = str(row.get("Suggested_Suit", ""))
            act_next = str(row.get("Actual_Next", ""))
            src = str(row.get("Source", "Statistical"))

            col_chk, col_info = st.columns([0.1, 0.9])
            with col_chk:
                if st.checkbox("", key=f"del_chk_{ts}_{idx}", label_visibility="collapsed"):
                    selected_timestamps.append(ts)
            with col_info:
                st.markdown(
                    f"`{ts}` · **{seq_str}** → {sug_suit} · actual **{act_next}** · _{src}_"
                )

        if selected_timestamps:
            if st.button(f"🗑️ Delete {len(selected_timestamps)} selected", type="primary", key="delete_selected_gamble"):
                if delete_gamble_records(selected_timestamps):
                    st.success(f"Deleted {len(selected_timestamps)} record(s).")
                    st.rerun()
                else:
                    st.error("Delete failed.")
        else:
            st.caption("No records selected.")
    else:
        st.info("No records yet.")

elif st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board")
    st.caption(
        "Per-slot ranking using KM-aware spin budgets, multi-hit rate, JJ tendency and post-big-win behaviour. "
        "AI-refined ranking still available below."
    )

    filtered_slots = []
    for s in st.session_state.slots_db:
        if s["slot"] in st.session_state.played_basket:
            continue
        rehit = s.get("rehit_metrics", {})
        first_total = rehit.get("first_hit_total", 0)
        if first_total > 5:
            filtered_slots.append(s)

    current_display = filtered_slots[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        b1 = item.get("budget_1st")
        b2 = item.get("budget_2nd")
        b3 = item.get("budget_3rd")
        multi = item.get("multi_hit_rate")
        table_data.append({
            "Rank": rank,
            "Family": item.get("family", "N/A"),
            "Slot": item.get("slot", "N/A"),
            "Play Style": item.get("play_style", "—"),
            "JJ Tendency": item.get("jj_tendency", "—"),
            "Multi-Hit %": f"{multi:.0f}%" if multi is not None else "—",
            "Budget 1st": int(b1) if b1 is not None else "—",
            "Budget 2nd": int(b2) if b2 is not None else "—",
            "Budget 3rd": int(b3) if b3 is not None else "—",
            "Post-Big Note": item.get("post_big_note", "—"),
            "Sample": item.get("sample_quality", "—"),
            "Check-in $": get_recommended_checkin(b1 if b1 is not None else item.get("spin_1st")),
        })

    df_priority = pd.DataFrame(table_data)

    st.markdown("### Statistical Ranking (enhanced)")
    if df_priority.empty:
        st.info("No slots with enough data.")
    else:
        csv_text = df_priority.to_csv(index=False, sep="\t")
        with st.expander("📋 Click here → Select All → Copy"):
            st.code(csv_text, language=None)

        st.dataframe(
            df_priority,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", width="small"),
                "Family": st.column_config.TextColumn("Family", width="medium"),
                "Slot": st.column_config.TextColumn("Slot", width="medium"),
                "Play Style": st.column_config.TextColumn("Play Style", width="medium"),
                "JJ Tendency": st.column_config.TextColumn("JJ Tendency", width="small"),
                "Multi-Hit %": st.column_config.TextColumn("Multi-Hit %", width="small"),
                "Budget 1st": st.column_config.NumberColumn("Budget 1st", width="small"),
                "Budget 2nd": st.column_config.NumberColumn("Budget 2nd", width="small"),
                "Budget 3rd": st.column_config.NumberColumn("Budget 3rd", width="small"),
                "Post-Big Note": st.column_config.TextColumn("Post-Big Note", width="medium"),
                "Sample": st.column_config.TextColumn("Sample", width="small"),
                "Check-in $": st.column_config.NumberColumn("Check-in $", width="small"),
            }
        )

    if len(filtered_slots) > st.session_state.display_limit:
        if st.button("➕ Load 15 More Slots"):
            st.session_state.display_limit += 15
            st.rerun()

    # === AI Priority Ranking ===
    st.markdown("---")
    st.markdown("### AI-Refined Priority Ranking")
    st.caption("The AI reviews the top statistical candidates and produces its own ranked list (Gemini → Groq fallback).")

    if st.button("🤖 Ask AI for Priority Ranking", key="ask_ai_priority", type="primary"):
        with st.spinner("AI is analysing all data and ranking the best machines for today (Gemini → Groq)..."):
            ai_text, provider = get_ai_priority_ranking(
                st.session_state.slots_db,
                st.session_state.selected_day,
                st.session_state.played_basket
            )
            if ai_text:
                parsed = parse_ai_priority_list(ai_text, st.session_state.slots_db)
                st.session_state.ai_priority_result = (parsed, provider, ai_text)
            else:
                st.session_state.ai_priority_result = (None, provider, None)
            st.rerun()

    if st.session_state.ai_priority_result:
        parsed_list, provider, raw_text = st.session_state.ai_priority_result
        if parsed_list:
            st.success(f"AI ranking ready ({provider}) — showing {len(parsed_list)} machines")

            ai_table = []
            for rank, item in enumerate(parsed_list, 1):
                b1 = item.get("budget_1st") or item.get("spin_1st")
                b2 = item.get("budget_2nd") or item.get("spin_2nd")
                b3 = item.get("budget_3rd") or item.get("spin_3rd")
                multi = item.get("multi_hit_rate")
                ai_table.append({
                    "Rank": rank,
                    "Family": item.get("family", "N/A"),
                    "Slot": item.get("slot", "N/A"),
                    "Play Style": item.get("play_style", "—"),
                    "JJ Tendency": item.get("jj_tendency", "—"),
                    "Multi-Hit %": f"{multi:.0f}%" if multi is not None else "—",
                    "Budget 1st": int(b1) if b1 is not None else "—",
                    "Budget 2nd": int(b2) if b2 is not None else "—",
                    "Budget 3rd": int(b3) if b3 is not None else "—",
                    "Post-Big Note": item.get("post_big_note", "—"),
                    "Sample": item.get("sample_quality", "—"),
                })

            df_ai = pd.DataFrame(ai_table)

            csv_ai = df_ai.to_csv(index=False, sep="\t")
            with st.expander("📋 Click here → Select All → Copy (AI Ranking)"):
                st.code(csv_ai, language=None)

            st.dataframe(
                df_ai,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Rank": st.column_config.NumberColumn("Rank", width="small"),
                    "Family": st.column_config.TextColumn("Family", width="medium"),
                    "Slot": st.column_config.TextColumn("Slot", width="medium"),
                    "Play Style": st.column_config.TextColumn("Play Style", width="medium"),
                    "JJ Tendency": st.column_config.TextColumn("JJ Tendency", width="small"),
                    "Multi-Hit %": st.column_config.TextColumn("Multi-Hit %", width="small"),
                    "Budget 1st": st.column_config.NumberColumn("Budget 1st", width="small"),
                    "Budget 2nd": st.column_config.NumberColumn("Budget 2nd", width="small"),
                    "Budget 3rd": st.column_config.NumberColumn("Budget 3rd", width="small"),
                    "Post-Big Note": st.column_config.TextColumn("Post-Big Note", width="medium"),
                    "Sample": st.column_config.TextColumn("Sample", width="small"),
                }
            )
        else:
            st.warning(f"AI response could not be fully parsed ({provider}). Raw response:")
            st.code(raw_text or "No response")

elif st.session_state.active_tab == "📈 Overall Performance":
    st.subheader("📈 Overall Performance (All Historical Logs)")
    st.caption("Sorted by 1st Hits / Total (success rate). Higher hit rate ranks first.")

    overall_slots = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rehit = compute_slot_rehit_metrics(slot, fam, live_sheet_df)
            first_total = rehit.get("first_hit_total", 0)
            if first_total < 10:
                continue
            first_hits = rehit.get("first_hit_count", 0)
            success_rate = (first_hits / first_total) if first_total > 0 else 0.0
            avg_1st_mult = rehit.get("avg_first_multiplier", 0.0) or 0.0
            avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0) or 0.0
            multi_hit_count = rehit.get("multi_hit_count", 0) or 0

            sample_factor = min(1.0, first_total / 40.0)
            hit_quality = 0.65 + (0.35 * success_rate)
            robust_score = avg_1st_mult * sample_factor * hit_quality
            if multi_hit_count >= 4 and avg_2nd_mult >= 30:
                robust_score *= 1.12
            if slot in ["Maximus Money", "Minotaur’s Treasure", "Ragnar the Great", "Fire Mountain", "El Matador"]:
                robust_score *= 1.15
            if slot in ["Golden Empress", "New York Nights"]:
                robust_score *= 0.70

            overall_slots.append({
                "family": fam,
                "slot": slot,
                "calc_success_rate": success_rate,
                "avg_first_multiplier": avg_1st_mult,
                "robust_score": robust_score,
                "rehit_metrics": rehit
            })

    # Sort by 1st Hits / Total (success rate) descending
    sorted_overall = sorted(
        overall_slots,
        key=lambda x: (
            x["calc_success_rate"],
            x["rehit_metrics"].get("first_hit_count", 0),
            x["robust_score"]
        ),
        reverse=True
    )

    table_data_overall = []
    for rank, item in enumerate(sorted_overall, 1):
        rehit = item["rehit_metrics"]
        att2_pop = rehit.get("attempt2_population", 0)
        first_hits = rehit.get("first_hit_count", 0)
        first_total = rehit.get("first_hit_total", 0)
        avg_1st_mult = rehit.get("avg_first_multiplier", 0.0)
        avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0)
        avg_spins = rehit.get("avg_first_spins", 0.0)
        success_pct = f"{round(item['calc_success_rate'] * 100, 1)}%"
        table_data_overall.append({
            "Rank": rank,
            "Slot Theme": item["slot"],
            "Family": item["family"],
            "Average Spin Count": f"{avg_spins}" if avg_spins > 0 else "N/A",
            "1st Hits/Total": f"{first_hits} / {first_total} ({success_pct})",
            "Avg 1st Mult": f"{avg_1st_mult}x" if avg_1st_mult > 0 else "N/A",
            "2nd Hits/Total": f"{rehit.get('multi_hit_count', 0)} / {att2_pop}",
            "Avg 2nd Mult": f"{avg_2nd_mult}x" if avg_2nd_mult > 0 else "N/A",
        })
    df_overall = pd.DataFrame(table_data_overall)
    if df_overall.empty:
        st.info("No slots with ≥ 10 total attempts found.")
    else:
        st.dataframe(df_overall, use_container_width=True, hide_index=True)

elif st.session_state.active_tab == "🤖 Interactive AI Agent":
    st.subheader("🤖 Slotpilot AI Assistant")
    with st.container():
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Active Day Focus", st.session_state.selected_day)
        m_col2.metric("Current Bankroll", f"${st.session_state.current_bankroll:.2f}")
        m_col3.metric("Target Goal", f"${st.session_state.session_target:.2f}")
        m_col4.metric("Played Today", f"{len(st.session_state.played_basket)} Machines")
    st.markdown("---")
    prompt_to_submit = None
    q_col1, q_col2, q_col3 = st.columns(3)
    with q_col1:
        if st.button("🏆 Top Priority Recommendation", use_container_width=True):
            prompt_to_submit = f"What are the top 3 best slots to play today ({st.session_state.selected_day})?"
    with q_col2:
        if st.button("🔥 High Repeat Multipliers", use_container_width=True):
            prompt_to_submit = "Which slots currently have the highest repeat-hit rate (>30%)?"
    with q_col3:
        if st.button("💵 Bankroll Strategy Check", use_container_width=True):
            prompt_to_submit = f"Given my current bankroll of ${st.session_state.current_bankroll:.2f}, guide my next play sequence."
    st.markdown("---")
    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    user_input = st.chat_input("Ask your Slotpilot AI...")
    if user_input:
        prompt_to_submit = user_input
    if prompt_to_submit:
        st.session_state.chat_messages.append({"role": "user", "content": prompt_to_submit})
        with st.chat_message("user"):
            st.markdown(prompt_to_submit)
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                response_text, provider = run_ai_agent(prompt_to_submit)
                st.caption(f"_Source: {provider}_")
                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})
        if st.session_state.get("pending_rerun"):
            st.session_state.pending_rerun = False
            st.rerun()

elif st.session_state.active_tab in ("🧺 Session & Basket", "🧺 Played Basket & Overrides"):
    st.subheader("🧺 Played Basket")
    if not st.session_state.played_basket:
        st.info("No machines marked as played yet today.")
    else:
        for slot in st.session_state.played_basket:
            col_p1, col_p2 = st.columns([3, 1])
            with col_p1:
                st.write(f"• **{slot}**")
            with col_p2:
                if st.button("Restore to Active", key=f"restore_{slot}"):
                    restore_slot(slot)
                    st.rerun()
        st.markdown("---")
        if st.button("🗑️ Clear Entire Played Basket"):
            st.session_state.played_basket = []
            persist_session_state()
            st.rerun()
