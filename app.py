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
    # "📝 Live Data Entry",
    "🤖 Interactive AI Agent",
    "🧺 Played Basket & Overrides"
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
    st.session_state.active_tab = "🃏 Gamble Analyzer"
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

def build_priority_dataset(live_df, target_day=None, strict_mode=True):
    records = []
    slot_scores = []
    if target_day is None:
        target_day = datetime.now().strftime("%A")

    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rvi_score, source_proof, active_day, day_factor, day_hits, total_hits = compute_75_25_rvi(slot, fam, live_df, target_day, strict_mode)
            rehit = compute_slot_rehit_metrics(slot, fam, live_df)

            spin_1st = get_spins_for_hit(slot, fam, live_df, hit_number=1, percentile=85)
            spin_2nd = get_spins_for_hit(slot, fam, live_df, hit_number=2, percentile=85)
            spin_3rd = get_spins_for_hit(slot, fam, live_df, hit_number=3, percentile=85)

            first_total = rehit.get("first_hit_total", 0) or 0
            first_hits = rehit.get("first_hit_count", 0) or 0
            avg_mult = rehit.get("avg_first_multiplier", 0.0) or 0.0
            max_mult = rehit.get("max_first_multiplier", 0.0) or 0.0
            multi_rate = rehit.get("multi_hit_rate", 0.0) or 0.0
            avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0) or 0.0
            avg_3rd_mult = rehit.get("avg_third_multiplier", 0.0) or 0.0
            max_2nd_mult = rehit.get("max_repeat_multiplier", 0.0) or 0.0

            if first_total < 3:
                composite = 0.0
            else:
                success_rate = first_hits / first_total if first_total > 0 else 0
                success_score = min(10.0, success_rate * 8.5)

                mult_score = min(13.0, (avg_mult / 5.0) + (max_mult / 22.0))

                if spin_1st is None:
                    spin_score = 4.5
                elif spin_1st <= 40:
                    spin_score = 9.0
                elif spin_1st <= 55:
                    spin_score = 7.0
                elif spin_1st <= 70:
                    spin_score = 4.8
                elif spin_1st <= 90:
                    spin_score = 2.5
                else:
                    spin_score = 1.0

                multi_size_bonus = min(4.5, 
                    (avg_2nd_mult / 16.0) + 
                    (max_2nd_mult / 30.0) + 
                    (avg_3rd_mult / 20.0) + 
                    (multi_rate / 45.0)
                )

                composite = (
                    0.12 * success_score +
                    0.48 * mult_score +
                    0.15 * spin_score +
                    0.25 * multi_size_bonus
                )

                if first_total < 5:
                    composite *= 0.88
                elif first_total < 8:
                    composite *= 0.95

            if slot in UPSIDE_BOOST:
                composite *= UPSIDE_BOOST[slot]
            if slot in GRINDER_PENALTY:
                composite *= GRINDER_PENALTY[slot]

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
                "spin_3rd": spin_3rd
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
            "spin_3rd": item["spin_3rd"]
        })
    return records

# ==========================================
# 2B. GAMBLE DATA ENGINE
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

def get_gamble_suggestion(sequence: list):
    df = load_gamble_data()
    if df.empty or "Actual_Next" not in df.columns:
        return {"color": "Red", "suit": "Hearts", "context_len": 0, "match_count": 0}

    df = df.dropna(subset=["Actual_Next"])
    df["Actual_Next"] = df["Actual_Next"].astype(str).str.strip()
    df = df[df["Actual_Next"].isin(SUITS)]
    if df.empty:
        return {"color": "Red", "suit": "Hearts", "context_len": 0, "match_count": 0}

    def most_common(counter, default=None):
        if not counter:
            return default
        return counter.most_common(1)[0][0]

    def seq_str(cards):
        return "-".join(cards)

    recent = df.tail(40) if len(df) > 40 else df
    extended = _build_extended_sequence(sequence, recent)
    context_len = len(extended)

    search_lengths = list(range(min(context_len, 9), 0, -1))

    for length in search_lengths:
        key = seq_str(extended[-length:])
        if "Sequence" not in df.columns:
            continue
        matches = df[df["Sequence"].astype(str).str.endswith(key)]
        if len(matches) >= 1:
            next_suits = matches["Actual_Next"].tolist()
            color_counter = Counter([SUIT_COLOR[s] for s in next_suits])
            preferred_color = most_common(color_counter)
            allowed = RED_SUITS if preferred_color == "Red" else BLACK_SUITS
            suit_counter = Counter([s for s in next_suits if s in allowed])
            if suit_counter:
                best_suit = most_common(suit_counter)
                return {
                    "color": preferred_color,
                    "suit": best_suit,
                    "context_len": context_len,
                    "match_count": len(matches)
                }

    global_colors = Counter([SUIT_COLOR[s] for s in df["Actual_Next"]])
    total = sum(global_colors.values())
    red_count = global_colors.get("Red", 0)
    black_count = global_colors.get("Black", 0)

    if total > 0 and abs(red_count - black_count) / total < 0.28:
        last_color = SUIT_COLOR.get(sequence[-1], "Red") if sequence else "Red"
        preferred_color = last_color
    else:
        preferred_color = most_common(global_colors, "Red")

    allowed_suits = RED_SUITS if preferred_color == "Red" else BLACK_SUITS
    global_suits = Counter([s for s in df["Actual_Next"] if s in allowed_suits])
    best_suit = most_common(global_suits, allowed_suits[0])

    return {
        "color": preferred_color,
        "suit": best_suit,
        "context_len": context_len,
        "match_count": 0
    }

# ==========================================
# 3. AI AGENT ENGINE
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

def get_ai_gamble_suggestion(sequence: list, extended: list):
    """Ask AI for a gamble suggestion based on the current + extended sequence."""
    try:
        client = get_gemini_client()
        if not client:
            return "AI unavailable (no API key).", "None"
        
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
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        return response.text or "No response", "Gemini"
    except Exception as e:
        return f"AI error: {e}", "None"

def get_ai_priority_ranking(slots_db, selected_day, played_basket):
    """Ask AI to re-rank the top machines for today."""
    try:
        client = get_gemini_client()
        if not client:
            return None, "None"
        
        # Prepare top candidates
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
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        return response.text or "", "Gemini"
    except Exception as e:
        return None, f"Error: {e}"

def parse_ai_priority_list(ai_text: str, slots_db: list):
    """Parse the AI numbered list back into slot records."""
    if not ai_text:
        return []
    
    lines = [l.strip() for l in ai_text.strip().splitlines() if l.strip()]
    parsed = []
    slot_lookup = {(s["family"].lower(), s["slot"].lower()): s for s in slots_db}
    
    for line in lines:
        # Expected: "1. Family | Slot Name"
        match = re.match(r"^\d+[\.\)]\s*(.+?)\s*\|\s*(.+)$", line)
        if not match:
            continue
        fam = match.group(1).strip()
        slot = match.group(2).strip()
        
        key = (fam.lower(), slot.lower())
        if key in slot_lookup:
            parsed.append(slot_lookup[key])
        else:
            # Fuzzy fallback
            for s in slots_db:
                if s["slot"].lower() == slot.lower():
                    parsed.append(s)
                    break
    return parsed

# ==========================================
# LOAD DATA & INITIALIZE STATE
# ==========================================
live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()
if "slots_db" not in st.session_state or not st.session_state.slots_db:
    st.session_state.slots_db = build_priority_dataset(
        live_sheet_df,
        st.session_state.selected_day,
        st.session_state.strict_day_penalty
    )

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
st.sidebar.subheader("💰 Bankroll & Risk")
with st.sidebar.form("bankroll_form"):
    new_start = st.number_input("Starting Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
    new_current = st.number_input("Current Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
    new_target = st.number_input("Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)
    bankroll_submit = st.form_submit_button("💾 Update & Save")
    if bankroll_submit:
        st.session_state.session_start_bankroll = new_start
        st.session_state.current_bankroll = new_current
        st.session_state.session_target = new_target
        persist_session_state()
        st.rerun()

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

if st.session_state.active_tab == "🃏 Gamble Analyzer":
    st.subheader("🃏 Gamble Analyzer")
    st.caption("Statistical engine looks further back + prefers frequent/recent matches. AI suggestion available.")

    st.markdown("### Enter the 5 cards (left → right)")
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
        st.markdown("#### Current sequence")
        html_parts = [suit_html(s) for s in seq]
        st.markdown(" &nbsp;→&nbsp; ".join(html_parts) + f" &nbsp;&nbsp;({len(seq)}/5)", unsafe_allow_html=True)
        if st.button("↺ Clear sequence (new machine)", key="clear_seq"):
            st.session_state.gamble_sequence = []
            st.session_state.ai_gamble_suggestion = None
            st.rerun()
    else:
        st.info("Click the four suit buttons above to enter the cards.")

    if len(seq) == 5:
        sug = get_gamble_suggestion(seq)
        df_full = load_gamble_data()
        recent = df_full.tail(100) if len(df_full) > 100 else df_full
        extended = _build_extended_sequence(seq, recent)

        st.markdown("---")
        st.markdown("### Statistical suggestion")
        
        col_sug, col_btn = st.columns([3, 1])
        with col_sug:
            st.markdown(
                f"**Colour** &nbsp;&nbsp; {color_html(sug['color'])}<br>"
                f"**Suit** &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {suit_html(sug['suit'])}",
                unsafe_allow_html=True
            )
            st.caption(f"Context used: {sug.get('context_len', 0)} cards | Matches found: {sug.get('match_count', 0)}")
        with col_btn:
            st.write("")
            if st.button("✅ Correct – Log this", key="quick_correct", use_container_width=True, type="primary"):
                actual = sug["suit"]
                now = datetime.now()
                record = {
                    "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "Date": now.strftime("%m/%d/%Y"),
                    "Day": now.strftime("%A"),
                    "Card1": seq[0],
                    "Card2": seq[1],
                    "Card3": seq[2],
                    "Card4": seq[3],
                    "Card5": seq[4],
                    "Sequence": "-".join(seq),
                    "Suggested_Color": sug["color"],
                    "Suggested_Suit": sug["suit"],
                    "Actual_Next": actual,
                    "Actual_Color": SUIT_COLOR[actual],
                }
                if append_gamble_record(record):
                    st.session_state.gamble_sequence = seq[1:] + [actual]
                    st.session_state.ai_gamble_suggestion = None
                    st.success("Logged as Correct. Sequence rolled forward.")
                    st.rerun()

        st.markdown("---")
        st.markdown("### AI suggestion (uses full history patterns)")
        
        if st.button("🤖 Ask AI for better suggestion", key="ask_ai_gamble"):
            with st.spinner("Analyzing full history patterns..."):
                ai_text, provider = get_ai_gamble_suggestion(seq, extended)
                st.session_state.ai_gamble_suggestion = (ai_text, provider)
                st.rerun()

        if st.session_state.ai_gamble_suggestion:
            ai_text, provider = st.session_state.ai_gamble_suggestion
            st.markdown(ai_text)
            st.caption(f"_Source: {provider}_")

        st.markdown("---")
        st.markdown("### Log the real next card (if both suggestions were wrong)")
        with st.form("log_gamble_result", clear_on_submit=False):
            actual = st.selectbox("Actual next card", options=SUITS, index=0, key="actual_select")
            submitted = st.form_submit_button("💾 Log & roll sequence forward", use_container_width=True)
            if submitted:
                now = datetime.now()
                record = {
                    "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "Date": now.strftime("%m/%d/%Y"),
                    "Day": now.strftime("%A"),
                    "Card1": seq[0],
                    "Card2": seq[1],
                    "Card3": seq[2],
                    "Card4": seq[3],
                    "Card5": seq[4],
                    "Sequence": "-".join(seq),
                    "Suggested_Color": sug["color"],
                    "Suggested_Suit": sug["suit"],
                    "Actual_Next": actual,
                    "Actual_Color": SUIT_COLOR[actual],
                }
                if append_gamble_record(record):
                    st.session_state.gamble_sequence = seq[1:] + [actual]
                    st.session_state.ai_gamble_suggestion = None
                    st.success("Logged. Sequence rolled forward.")
                    st.rerun()

    st.markdown("---")
    st.markdown("### Recent log (last 12)")
    gdf = load_gamble_data()
    if not gdf.empty:
        show_cols = [c for c in ["Timestamp", "Sequence", "Suggested_Color", "Suggested_Suit", "Actual_Next", "Actual_Color"] if c in gdf.columns]
        st.dataframe(gdf[show_cols].tail(12).iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.info("No records yet.")

elif st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board")
    st.caption("Statistical ranking + AI-refined ranking available")

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
        spin1 = item.get("spin_1st")
        table_data.append({
            "Rank": rank,
            "Family": item.get("family", "N/A"),
            "Slot": item.get("slot", "N/A"),
            "Spin required for first hit": spin1 if spin1 is not None else "—",
            "Spin needed for 2nd hit": item.get("spin_2nd") if item.get("spin_2nd") is not None else "—",
            "Spin needed for 3rd hit": item.get("spin_3rd") if item.get("spin_3rd") is not None else "—",
            "Recommended Max Check-in": get_recommended_checkin(spin1),
        })

    df_priority = pd.DataFrame(table_data)

    st.markdown("### Statistical Ranking")
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
                "Spin required for first hit": st.column_config.NumberColumn("Spin required for first hit", width="medium"),
                "Spin needed for 2nd hit": st.column_config.NumberColumn("Spin needed for 2nd hit", width="medium"),
                "Spin needed for 3rd hit": st.column_config.NumberColumn("Spin needed for 3rd hit", width="medium"),
                "Recommended Max Check-in": st.column_config.NumberColumn("Recommended Max Check-in", width="medium"),
            }
        )

    if len(filtered_slots) > st.session_state.display_limit:
        if st.button("➕ Load 15 MoreSlots"):
            st.session_state.display_limit += 15
            st.rerun()

    # === AI Priority Ranking ===
    st.markdown("---")
    st.markdown("### AI-Refined Priority Ranking")
    st.caption("The AI reviews the top statistical candidates and produces its own ranked list of the best 40–50 machines for today.")

    if st.button("🤖 Ask AI for Priority Ranking", key="ask_ai_priority", type="primary"):
        with st.spinner("AI is analysing all data and ranking the best machines for today..."):
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
                spin1 = item.get("spin_1st")
                ai_table.append({
                    "Rank": rank,
                    "Family": item.get("family", "N/A"),
                    "Slot": item.get("slot", "N/A"),
                    "Spin required for first hit": spin1 if spin1 is not None else "—",
                    "Spin needed for 2nd hit": item.get("spin_2nd") if item.get("spin_2nd") is not None else "—",
                    "Spin needed for 3rd hit": item.get("spin_3rd") if item.get("spin_3rd") is not None else "—",
                    "Recommended Max Check-in": get_recommended_checkin(spin1),
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
                    "Spin required for first hit": st.column_config.NumberColumn("Spin required for first hit", width="medium"),
                    "Spin needed for 2nd hit": st.column_config.NumberColumn("Spin needed for 2nd hit", width="medium"),
                    "Spin needed for 3rd hit": st.column_config.NumberColumn("Spin needed for 3rd hit", width="medium"),
                    "Recommended Max Check-in": st.column_config.NumberColumn("Recommended Max Check-in", width="medium"),
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

elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
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
