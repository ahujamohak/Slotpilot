import os
import re
import math
import numpy as np
import pandas as pd
from datetime import datetime
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

# Google Sheets Connection
conn = st.connection("gsheets", type=GSheetsConnection)

# Model / worksheet constants — keep these in one place so a future
# provider deprecation is a one-line fix instead of a code hunt.
GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile was retired Aug 16 2026
SESSION_STATE_WORKSHEET = "Live Session"
SESSION_LOG_WORKSHEET = "Session Log"

TAB_OPTIONS = [
    "📊 Today's Priority Board",
    "📋 Pre-Planned Execution Cards",
    "📝 Live Data Entry",
    "🤖 Interactive AI Agent",
    "🧺 Played Basket & Overrides"
]

# ==========================================
# 0B. SESSION PERSISTENCE (survives dropped mobile connections)
# ==========================================
# Streamlit's session_state lives per-WebSocket-connection. A phone
# screen lock / tab switch / network drop can silently reset bankroll,
# played basket, etc. We mirror the essentials to a "Live Session"
# worksheet so a reconnect on the SAME calendar day can restore them.
# If that worksheet doesn't exist yet, this fails soft and the app
# behaves exactly as before (fresh state each load).

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
            "Max Risk Pct": st.session_state.get("max_risk_pct", 20),
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
    st.session_state.active_tab = "📊 Today's Priority Board"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []
    st.session_state.selected_day = datetime.now().strftime("%A")
    st.session_state.max_risk_pct = 20
    st.session_state.last_saved_ts = None
    st.session_state.last_save_error = None
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
        st.session_state.active_tab = "📊 Today's Priority Board"
        strict_raw = restored.get("Strict Day Penalty", True)
        st.session_state.strict_day_penalty = str(strict_raw).strip().lower() in ("true", "1", "yes")
        st.session_state.chat_messages = []
        st.session_state.selected_day = str(restored.get("Selected Day") or datetime.now().strftime("%A"))
        try:
            st.session_state.max_risk_pct = int(float(restored.get("Max Risk Pct", 20) or 20))
        except (ValueError, TypeError):
            st.session_state.max_risk_pct = 20
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
if "max_risk_pct" not in st.session_state:
    st.session_state.max_risk_pct = 20

# Helper Functions for Basket Management
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
# 1. MASTER LIST & MULTI-PHASE CONFIG
# ==========================================

VALID_SLOT_BETS = [1.00, 1.25, 1.50, 2.00, 2.50, 3.00, 3.75, 5.00, 6.25, 7.50, 10.00]

def snap_to_valid_bet(bet: float) -> float:
    """Snaps any arbitrary calculated bet to the nearest valid slot bet denomination."""
    return min(VALID_SLOT_BETS, key=lambda x: abs(x - bet))

CUSTOM_HIT_ZONES = {
    "New York Nights": {
        "phases": [
            {"spins": 15, "bet": 7.50, "note": "Early Trigger Zone (Spin 15 Hit)"},
            {"spins": 5,  "bet": 5.00, "note": "Dead Spin Filter (Stop if 0x)"},
            {"spins": 15, "bet": 3.75, "note": "Mid-Cycle Transition"},
            {"spins": 15, "bet": 7.50, "note": "Late Trigger Zone (Spin 48 Hit)"}
        ]
    }
}

SLOT_MASTER_LIST = {
    "All Aboard The Lucky Link": ["Go West", "Shinobi"],
    "Balloon Link": ["Australian Outback", "Skull Island"],
    "Bau Zhu Zhao Fu": ["Blue Festival", "Red Festival"],
    "Bull Rush Blitz": ["Golden Empress", "Wild Outback", "Yarr Matey"],
    "Bull Rush Blitz 2 Multi": ["Maximus Money", "New York Nights", "Roses & Riches"],
    "Bull Rush Blitz 3 Multi": ["El Metador"],
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
# 2. SHEET DATA INSPECTION & CALCULATION ENGINE
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

def parse_session_log_data(live_df, slot_name, family_name):
    """
    Parses live sheet logs for a specific slot/family to clean spins,
    distinguish exact hits from censored exit entries (32+), and extract win metrics.
    """
    if live_df.empty:
        return pd.DataFrame()

    cols = {str(c).lower(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin") or cols.get("spins")
    attempt_col = cols.get("attempt number") or cols.get("attempt")
    hit_num_col = cols.get("hit number") or cols.get("hit")
    mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    win_amt_col = cols.get("win amount") or cols.get("win ($)")
    day_col = cols.get("day") or cols.get("day of week")

    matched = live_df.copy()

    # Dual filter on Family AND Slot Name to avoid cross-contamination
    has_slot = slot_col and slot_col in matched.columns
    has_fam = fam_col and fam_col in matched.columns

    if has_slot and has_fam:
        dual_matched = matched[
            (matched[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
            (matched[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
        ]
        if not dual_matched.empty:
            matched = dual_matched
        else:
            matched = matched[matched[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]
    elif has_slot:
        matched = matched[matched[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]
    elif has_fam:
        matched = matched[matched[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower()]

    if matched.empty:
        return pd.DataFrame()

    # Extract raw string spin
    spin_raw = matched[spin_col].astype(str).str.strip() if spin_col else pd.Series(["0"] * len(matched))

    # 1. Flag censored data ('32+' means no feature hit, exited after those spins)
    matched["_is_censored"] = spin_raw.str.contains(r'\+', regex=True)

    # 2. Clean numeric spin count
    matched["_spins"] = pd.to_numeric(spin_raw.str.extract(r'(\d+)')[0], errors='coerce').fillna(0)

    # 3. Clean numeric attempt, hit number, multiplier, and win amount
    matched["_attempt"] = pd.to_numeric(matched[attempt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(1) if attempt_col else 1
    matched["_hit"] = pd.to_numeric(matched[hit_num_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0) if hit_num_col else 0
    matched["_mult"] = pd.to_numeric(matched[mult_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0) if mult_col else 0
    matched["_win_amt"] = pd.to_numeric(matched[win_amt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0) if win_amt_col else 0
    matched["_day"] = matched[day_col].astype(str).str.strip() if day_col else ""

    return matched

# ==========================================
# CALCULATION ENGINE HELPERS (REHIT UPDATE)
# ==========================================

def compute_slot_rehit_metrics(slot_name, family_name, live_df):
    default_res = {
        "repeat_sample_size": 0,
        "attempt2_population": 0,
        "multi_hit_count": 0,
        "multi_hit_rate": 0.0,
        "avg_repeat_multiplier": 0.0,
        "max_repeat_multiplier": 0.0,
        "avg_attempt2_spins": 0.0,
        "repeat_recommendation": "No Repeat Data (Follow Baseline Probe)"
    }

    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return default_res

    total_logs = len(parsed_df)

    # The population that COULD have produced a repeat hit is every row
    # where a second attempt was actually made (Attempt Number == 2),
    # regardless of whether that second attempt hit or not.
    attempt2_rows = parsed_df[parsed_df["_attempt"] == 2]
    attempt2_population = len(attempt2_rows)

    # Strict 2nd feature match: Attempt Number == 2 AND Hit Number == 2
    repeat_entries = parsed_df[(parsed_df["_attempt"] == 2) & (parsed_df["_hit"] == 2)]
    repeat_count = len(repeat_entries)

    if total_logs == 0:
        return default_res

    # multi_hit_rate = P(2nd attempt hits | a 2nd attempt was made).
    # Denominator is attempt2_population, NOT total_logs — total_logs
    # includes every first-attempt-only row that never had a chance to
    # repeat, which previously diluted this rate and understated it.
    if attempt2_population > 0:
        multi_hit_rate = round((repeat_count / attempt2_population) * 100.0, 1)
    else:
        multi_hit_rate = 0.0

    # Multiplier stats on 2nd feature hits
    avg_repeat_mult = round(repeat_entries["_mult"].mean(), 1) if not repeat_entries.empty else 0.0
    max_repeat_mult = round(repeat_entries["_mult"].max(), 1) if not repeat_entries.empty else 0.0

    # Average spins to trigger 2nd feature (Attempt == 2 AND Hit == 2)
    att2_hits = repeat_entries[(repeat_entries["_spins"] > 0) & (~repeat_entries["_is_censored"])]
    avg_att2_spins = round(att2_hits["_spins"].mean(), 1) if not att2_hits.empty else 0.0

    if attempt2_population == 0:
        recommendation = "ℹ️ UNTESTED REPEAT PROFILE: No second attempt (Attempt 2) logged yet."
    elif multi_hit_rate >= 40.0:
        recommendation = f"🔥 HIGH REPEAT POTENTIAL ({multi_hit_rate}% of {attempt2_population} 2nd attempts hit): Reset to Phase 1 immediately after feature hit. (Attempt 2 avg trigger: {avg_att2_spins if avg_att2_spins > 0 else 'N/A'} spins)."
    elif multi_hit_rate >= 20.0:
        recommendation = f"⚡ MODERATE REPEAT POTENTIAL ({multi_hit_rate}% of {attempt2_population} 2nd attempts hit): Finish current phase; re-probe if win > 20x."
    elif repeat_count > 0:
        recommendation = f"⚠️ LOW REPEAT POTENTIAL ({multi_hit_rate}% of {attempt2_population} 2nd attempts hit): Single hit machine. Lock profits and exit."
    else:
        recommendation = f"⚠️ NO REPEATS YET ({attempt2_population} 2nd attempts logged, 0 hit): Lock profits and exit."

    return {
        "repeat_sample_size": total_logs,
        "attempt2_population": attempt2_population,
        "multi_hit_count": repeat_count,
        "multi_hit_rate": multi_hit_rate,
        "avg_repeat_multiplier": avg_repeat_mult,
        "max_repeat_multiplier": max_repeat_mult,
        "avg_attempt2_spins": avg_att2_spins,
        "repeat_recommendation": recommendation
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

    if "_day" in parsed_df.columns:
        day_matches = parsed_df[parsed_df["_day"].str.lower() == str(target_day).strip().lower()]
        day_log_count = len(day_matches)

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
            else:
                if strict_mode:
                    if total_logs >= 5:
                        day_factor = 0.40
                    elif total_logs >= 3:
                        day_factor = 0.55
                    else:
                        day_factor = 0.75
                else:
                    day_factor = 0.90

    # Filter for exact hits (excluding censored non-hits)
    actual_hits = parsed_df[(parsed_df["_hit"] > 0) & (~parsed_df["_is_censored"])]
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

def get_multi_phase_execution(slot_name, family_name, rvi_score, live_df):
    """
    Dynamically generates phase spin boundaries and bet sizing based on:
    1. Exact feature hit spin distribution vs Censored (32+) exit thresholds.
    2. Multiplier strength (high multiplier = scale up bet sizing).
    3. Concentrated hit windows (e.g. 1-15, 16-30, 31-45, 46+ spins).
    Note: this returns BASE phase sizing driven only by the slot's own
    history. Bankroll-aware scaling is applied separately at render time
    via scale_phases_for_bankroll(), so this function stays reusable
    regardless of live bankroll.
    """
    if slot_name in CUSTOM_HIT_ZONES:
        phases = CUSTOM_HIT_ZONES[slot_name]["phases"]
        total_spins = sum(p["spins"] for p in phases)
        raw_alloc = sum(p["spins"] * p["bet"] for p in phases)
        checkin_alloc = float(math.ceil(raw_alloc / 25.0) * 25)
        return phases, total_spins, checkin_alloc

    parsed_df = parse_session_log_data(live_df, slot_name, family_name)

    # Exact hits dataframe
    exact_hits = parsed_df[(parsed_df["_hit"] > 0) & (~parsed_df["_is_censored"])] if not parsed_df.empty else pd.DataFrame()
    censored_entries = parsed_df[parsed_df["_is_censored"]] if not parsed_df.empty else pd.DataFrame()

    # Determine Base Bet Scaling according to Volatility / Multipliers
    avg_mult = exact_hits["_mult"].mean() if not exact_hits.empty else 20.0
    if avg_mult >= 80.0:
        base_bet, high_bet, low_bet = 5.00, 10.00, 3.75
    elif avg_mult >= 40.0:
        base_bet, high_bet, low_bet = 3.75, 7.50, 2.50
    elif avg_mult >= 20.0:
        base_bet, high_bet, low_bet = 2.50, 5.00, 1.25
    else:
        base_bet, high_bet, low_bet = 1.25, 2.50, 0.75

    # Enforce snapping on base tiers to keep dynamic phase calculations on valid bet sizes
    base_bet = snap_to_valid_bet(base_bet)
    high_bet = snap_to_valid_bet(high_bet)
    low_bet = snap_to_valid_bet(low_bet)
    
    # If no hit data available, fall back to RVI-driven default generic bands
    if exact_hits.empty:
        max_boundary = int(censored_entries["_spins"].max()) if not censored_entries.empty else 45
        max_boundary = max(max_boundary, 35)

        step = math.ceil(max_boundary / 3)
        phases = [
            {"spins": step, "bet": low_bet, "note": "Initial Probe Phase"},
            {"spins": step, "bet": base_bet, "note": "Target Evaluation Zone"},
            {"spins": max_boundary - (step * 2), "bet": low_bet, "note": f"Late Checkpoint (Exit Threshold ~{max_boundary}s)"}
        ]
    else:
        # Determine maximum target spin threshold from high hits or censored exits
        max_hit_spin = exact_hits["_spins"].max()
        max_censored_spin = censored_entries["_spins"].max() if not censored_entries.empty else 0
        target_max_spin = int(max(max_hit_spin, max_censored_spin, 30))

        # Check win concentration across 4 potential spin windows
        hit_spins = exact_hits["_spins"]
        w1_hits = len(hit_spins[hit_spins <= 15])
        w2_hits = len(hit_spins[(hit_spins > 15) & (hit_spins <= 30)])
        w3_hits = len(hit_spins[(hit_spins > 30) & (hit_spins <= 45)])
        w4_hits = len(hit_spins[hit_spins > 45])

        total_exact = len(exact_hits)

        # Build dynamic 3-to-4 phase breakdown based on concentration
        phases = []

        # Window 1 (1–15 Spins)
        w1_spins = min(15, target_max_spin)
        w1_bet = high_bet if (w1_hits / total_exact) >= 0.40 else low_bet
        w1_note = "🔥 High-Hit Concentration Zone" if w1_bet == high_bet else "Initial Probe Zone"
        phases.append({"spins": w1_spins, "bet": w1_bet, "note": w1_note})

        # Window 2 (16–30 Spins)
        if target_max_spin > 15:
            w2_spins = min(15, target_max_spin - 15)
            w2_bet = high_bet if (w2_hits / total_exact) >= 0.30 else base_bet
            w2_note = "🔥 Peak Hit Concentration Zone" if w2_bet == high_bet else "Mid-Cycle Transition"
            phases.append({"spins": w2_spins, "bet": w2_bet, "note": w2_note})

        # Window 3 (31–45 Spins)
        if target_max_spin > 30:
            w3_spins = min(15, target_max_spin - 30)
            w3_bet = high_bet if (w3_hits / total_exact) >= 0.30 else low_bet
            w3_note = "🔥 Late Hit Concentration Zone" if w3_bet == high_bet else "Late Checkpoint (Exit Prep)"
            phases.append({"spins": w3_spins, "bet": w3_bet, "note": w3_note})

        # Window 4 (46+ Spins, if high spin threshold exists)
        if target_max_spin > 45:
            w4_spins = target_max_spin - 45
            w4_bet = high_bet if (w4_hits / total_exact) >= 0.25 else low_bet
            w4_note = "Extended Deep Trigger Zone" if w4_bet == high_bet else "Final Exit Checkpoint"
            phases.append({"spins": w4_spins, "bet": w4_bet, "note": w4_note})

    # Clean zero or negative spin phases
    phases = [p for p in phases if p["spins"] > 0]

    total_spins = sum(p["spins"] for p in phases)
    raw_alloc = sum(p["spins"] * p["bet"] for p in phases)
    checkin_alloc = float(math.ceil(raw_alloc / 25.0) * 25)
    return phases, total_spins, checkin_alloc

def build_priority_dataset(live_df, target_day=None, strict_mode=True):
    records = []
    slot_scores = []
    if target_day is None:
        target_day = datetime.now().strftime("%A")

    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rvi_score, source_proof, active_day, day_factor, day_hits, total_hits = compute_75_25_rvi(slot, fam, live_df, target_day, strict_mode)
            rehit_metrics = compute_slot_rehit_metrics(slot, fam, live_df)

            slot_scores.append({
                "family": fam,
                "slot": slot,
                "rvi": rvi_score,
                "source_proof": source_proof,
                "target_day": active_day,
                "day_factor": day_factor,
                "day_hits": day_hits,
                "total_hits": total_hits,
                "rehit_metrics": rehit_metrics
            })

    slot_scores = sorted(slot_scores, key=lambda x: (x["rvi"], x["rehit_metrics"]["multi_hit_rate"], x["day_hits"]), reverse=True)

    for item in slot_scores:
        fam = item["family"]
        slot = item["slot"]
        rvi_score = item["rvi"]

        phases, total_spins, checkin_alloc = get_multi_phase_execution(slot, fam, rvi_score, live_df)
        phase_breakdown_str = " | ".join([f"P{i+1}: {p['spins']}s @ ${p['bet']:.2f}" for i, p in enumerate(phases)])

        records.append({
            "family": fam,
            "slot": slot,
            "base_rvi": rvi_score,
            "phases": phases,
            "num_phases": len(phases),
            "phase_breakdown": phase_breakdown_str,
            "total_spins": total_spins,
            "checkin_alloc": checkin_alloc,
            "source_proof": item["source_proof"],
            "target_day": item["target_day"],
            "day_factor": item["day_factor"],
            "day_hits": item["day_hits"],
            "total_hits": item["total_hits"],
            "rehit_metrics": item["rehit_metrics"]
        })
    return sorted(records, key=lambda x: (x["base_rvi"], x["rehit_metrics"]["multi_hit_rate"]), reverse=True)

# ==========================================
# 2B. BANKROLL-AWARE STAKE SCALING
# ==========================================
# Phase spin-windows are calibrated from each slot's own hit-spin
# history (get_multi_phase_execution) and stay fixed regardless of
# bankroll. Bet SIZE within those windows is scaled here, at render
# time, against where the live session actually stands — so the same
# slot recommends smaller bets when the bankroll is down and caps any
# single check-in at a configurable % of current bankroll.

def compute_bankroll_scale():
    start = max(st.session_state.session_start_bankroll, 1.0)
    current = max(st.session_state.current_bankroll, 0.0)
    target = st.session_state.session_target

    bankroll_ratio = current / start
    target_span = max(target - start, 1.0)
    progress_ratio = (current - start) / target_span

    if progress_ratio >= 1.0:
        return 0.5, "🎯 Target reached or exceeded — bets scaled to 50% to protect winnings."
    elif bankroll_ratio <= 0.5:
        return 0.5, "🛑 Bankroll down 50%+ from session start — bets scaled to 50% to preserve capital."
    elif bankroll_ratio <= 0.75:
        return 0.75, "⚠️ Bankroll down 25%+ from session start — bets scaled to 75%."
    else:
        return 1.0, "Normal staking — no drawdown adjustment active."

def scale_phases_for_bankroll(phases, checkin_alloc):
    scale, posture_note = compute_bankroll_scale()
    current = max(st.session_state.current_bankroll, 0.0)
    max_risk_pct = st.session_state.get("max_risk_pct", 20) / 100.0
    risk_cap = current * max_risk_pct

    # Change 1: Snap bets after scaling by bankroll posture
    scaled_phases = [dict(p, bet=snap_to_valid_bet(p["bet"] * scale)) for p in phases]
    raw_alloc = sum(p["spins"] * p["bet"] for p in scaled_phases)

    cap_note = None
    if risk_cap > 0 and raw_alloc > risk_cap:
        cap_ratio = risk_cap / raw_alloc
        # Change 2: Snap bets again if risk cap forces a reduction
        scaled_phases = [dict(p, bet=snap_to_valid_bet(p["bet"] * cap_ratio)) for p in scaled_phases]
        raw_alloc = sum(p["spins"] * p["bet"] for p in scaled_phases)
        cap_note = f"Capped to {max_risk_pct*100:.0f}% of current bankroll (${risk_cap:.2f})."

    new_checkin = float(math.ceil(raw_alloc / 25.0) * 25) if raw_alloc > 0 else 0.0
    return scaled_phases, new_checkin, posture_note, cap_note
    
# ==========================================
# 3. AI AGENT ENGINE & TOOLS (Gemini primary, Groq fallback)
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
    """Marks a machine as played, moving it out of active recommendations and into the played basket."""
    return mark_slot_played(slot_name)

def tool_update_bankroll(new_amount: float) -> str:
    """Updates the user's current bankroll during a live casino session."""
    st.session_state.current_bankroll = float(new_amount)
    persist_session_state()
    return f"Current bankroll updated to ${new_amount:.2f}"

AVAILABLE_TOOLS = {
    "tool_mark_machine_played": tool_mark_machine_played,
    "tool_update_bankroll": tool_update_bankroll,
}

def build_agent_context():
    """Shared context builder so both Gemini and the Groq fallback see
    the same live picture — including bankroll-scaled $ amounts, not
    the raw generic ones, so AI-suggested bets match what the boards show."""
    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]

    slot_context_summary = []
    for s in available_slots[:20]:
        scaled_phases, scaled_checkin, _, cap_note = scale_phases_for_bankroll(s["phases"], s["checkin_alloc"])
        scaled_breakdown = " | ".join([f"P{i+1}: {p['spins']}s @ ${p['bet']:.2f}" for i, p in enumerate(scaled_phases)])
        slot_context_summary.append({
            "slot": s["slot"],
            "family": s["family"],
            "rvi_score": s["base_rvi"],
            "multi_hit_rate": f"{s['rehit_metrics']['multi_hit_rate']}%",
            "multi_hit_count": s['rehit_metrics']['multi_hit_count'],
            "attempt2_population": s['rehit_metrics'].get('attempt2_population', 0),
            "phase_plan_bankroll_scaled": scaled_breakdown,
            "checkin_alloc_bankroll_scaled": scaled_checkin,
            "recommendation_protocol": s['rehit_metrics']['repeat_recommendation']
        })

    scale, posture_note = compute_bankroll_scale()
    system_instruction = f"""
    You are an expert AI Casino Slot Optimization & Execution Agent.

    CURRENT LIVE SESSION ENVIRONMENT:
    - Active Target Day: {st.session_state.selected_day}
    - Current Active Bankroll: ${st.session_state.current_bankroll:.2f}
    - Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}
    - Target Bankroll: ${st.session_state.session_target:.2f}
    - Staking Posture: {posture_note}
    - Max Risk Per Check-In: {st.session_state.get('max_risk_pct', 20)}% of current bankroll
    - Strict Day Penalty Mode: {'ON' if st.session_state.strict_day_penalty else 'OFF'}
    - Played Basket (Played Today): {st.session_state.played_basket}

    AVAILABLE TOP-RANKED SLOTS DATASET (Ranked by 75/25 Hybrid Day-RVI & Multi-Hit Rate).
    Bet sizes below are ALREADY bankroll-adjusted — use them as-is, do not re-scale further:
    {slot_context_summary}

    OPERATIONAL INSTRUCTIONS:
    1. Reason through user questions dynamically using the live slot dataset provided above.
    2. When asked for N recommendations (e.g., "Suggest 3 best slots for today"), extract the top N unplayed machines from the dataset, provide their bankroll-scaled phase plans, and detail the post-hit repeat execution protocol.
    3. Be precise with mathematical references (Day-RVI scores, Multi-Hit Rates, spin counts, and bet sizes).
    4. You have access to tool function calls to mark machines played or update bankrolls directly if requested by the user.
    """
    return system_instruction

def run_gemini_agent(user_prompt: str):
    """Runs the primary agent via Gemini with MANUAL function-calling
    (automatic function calling explicitly disabled) so we can:
      (a) avoid the ambiguity of whether the SDK already auto-executed
          the tool before we check response.function_calls, and
      (b) send the tool's result back to the model for a proper
          follow-up turn, instead of short-circuiting with a raw
          string and dropping the rest of the user's question.
    Returns (response_text, state_changed) or raises on failure so the
    caller can fall back to Groq.
    """
    client = get_gemini_client()
    if not client:
        raise RuntimeError("GEMINI_API_KEY is not set in environment or Streamlit secrets.")

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
        # Execute every requested tool call, then send the results back
        # to the model in a follow-up turn so it can weave the action
        # into a full natural-language answer instead of us returning
        # a bare "Tool Action: ..." string.
        contents.append(response.candidates[0].content)
        function_response_parts = []
        for fn in response.function_calls:
            handler = AVAILABLE_TOOLS.get(fn.name)
            if handler is None:
                tool_result = f"Unknown tool '{fn.name}'."
            else:
                args = dict(fn.args) if fn.args else {}
                tool_result = handler(**args)
                state_changed = True
            function_response_parts.append(
                types.Part.from_function_response(name=fn.name, response={"result": tool_result})
            )
        contents.append(types.Content(role="user", parts=function_response_parts))

        follow_up = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        return follow_up.text or "🤖 Action completed.", state_changed

    return response.text, state_changed

def run_groq_agent(user_prompt: str):
    """Text-only fallback (no tool calling yet) — used only when Gemini
    is unavailable, rate-limited, or errors out. Clearly labelled in the
    UI so it's obvious which provider actually answered."""
    client = get_groq_client()
    if not client:
        return "⚠️ Groq fallback unavailable: `GROQ_API_KEY` is not set."

    system_instruction = build_agent_context()
    system_instruction += "\n\nNOTE: You are running as a fallback text-only assistant. You cannot mark machines played or update the bankroll directly — tell the user to do that manually in the app if asked."

    messages = [{"role": "system", "content": system_instruction}]
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_prompt})

    completion = client.chat.completions.create(model=GROQ_MODEL, messages=messages, temperature=0.3)
    return completion.choices[0].message.content

def run_ai_agent(user_prompt: str):
    """Primary/fallback orchestration. Returns (response_text, provider_label)."""
    try:
        text, state_changed = run_gemini_agent(user_prompt)
        if state_changed:
            st.session_state.pending_rerun = True
        return text, "Gemini"
    except Exception as gemini_err:
        try:
            text = run_groq_agent(user_prompt)
            return f"{text}\n\n_(⚠️ Gemini was unavailable — answered via Groq fallback, no live actions taken. Gemini error: {gemini_err})_", "Groq (fallback)"
        except Exception as groq_err:
            return f"⚠️ Both AI providers failed.\n- Gemini: {gemini_err}\n- Groq: {groq_err}", "None"

# ==========================================
# LOAD DATA & INITIALIZE STATE DATASET
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
    st.sidebar.info("♻️ Restored today's session (bankroll, basket, settings) from your last connection.")

# ONE-BUTTON GLOBAL RESET
if st.sidebar.button("🔄 Reset All Session Data", use_container_width=True, type="primary"):
    reset_all_state()
    st.rerun()

st.sidebar.markdown("---")

if detected_sheet_cols:
    st.sidebar.success(f"🟢 GSheet Connected ({len(detected_sheet_cols)} Cols)")
else:
    st.sidebar.warning("🟡 GSheet Off-line")

if st.session_state.get("last_saved_ts"):
    st.sidebar.caption(f"💾 Session last saved: {st.session_state.last_saved_ts}")
if st.session_state.get("last_save_error"):
    st.sidebar.caption(f"⚠️ Last save failed: {st.session_state.last_save_error}")

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

# Wrapped in a form so typing/dragging on mobile doesn't fire a save
# (and a Google Sheets write) on every keystroke — only on submit.
with st.sidebar.form("bankroll_form"):
    new_start = st.number_input("Starting Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
    new_current = st.number_input("Current Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
    new_target = st.number_input("Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)
    new_risk_pct = st.slider("Max Risk per Check-In (% of current bankroll)", min_value=5, max_value=50, value=int(st.session_state.max_risk_pct), step=5)
    bankroll_submit = st.form_submit_button("💾 Update & Save")

    if bankroll_submit:
        st.session_state.session_start_bankroll = new_start
        st.session_state.current_bankroll = new_current
        st.session_state.session_target = new_target
        st.session_state.max_risk_pct = new_risk_pct
        persist_session_state()
        st.rerun()

_scale, _posture = compute_bankroll_scale()
st.sidebar.caption(f"Staking posture: {_posture}")

# ==========================================
# 5. DASHBOARD VIEWS
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")
st.caption(f"Active View: **{st.session_state.active_tab}** | Target Day Context: **{st.session_state.selected_day}** | Strict Mode: **{'ON' if st.session_state.strict_day_penalty else 'OFF'}**")
st.markdown("---")

# ==========================================
# TAB 1: TODAY'S PRIORITY BOARD
# ==========================================

if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader(f"Today's Priority Board (Day & Dynamic Spin-Calibrated Matrix for {st.session_state.selected_day})")

    scale, posture_note = compute_bankroll_scale()
    if scale < 1.0:
        st.warning(f"**Staking posture active:** {posture_note} Bet sizes below already reflect this.")
    else:
        st.info(f"**Staking posture:** {posture_note}")

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    current_display = available_slots[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        rehit = item.get("rehit_metrics", {})
        avg_att2 = rehit.get("avg_attempt2_spins", 0.0)
        scaled_phases, scaled_checkin, _, cap_note = scale_phases_for_bankroll(item.get("phases", []), item.get("checkin_alloc", 0.0))
        scaled_breakdown = " | ".join([f"P{i+1}: {p['spins']}s @ ${p['bet']:.2f}" for i, p in enumerate(scaled_phases)])

        att2_pop = rehit.get("attempt2_population", 0)
        table_data.append({
            "Rank": rank,
            "Slot Family": item.get("family", "N/A"),
            "Slot Theme Name": item.get("slot", "N/A"),
            "Day-RVI Score": item.get("base_rvi", 0.0),
            "Repeat-Hit Rate (of 2nd attempts)": f"{rehit.get('multi_hit_rate', 0.0)}%",
            "2nd-Attempt Hits/Total": f"{rehit.get('multi_hit_count', 0)} / {att2_pop}",
            "Avg Attempt 2 Trigger": f"{avg_att2:.1f}s" if avg_att2 > 0 else "N/A",
            "Avg Repeat Win": f"{rehit.get('avg_repeat_multiplier', 0.0)}x",
            "Bankroll-Scaled Phase Plan": scaled_breakdown,
            "Check-In Alloc ($, bankroll-scaled)": f"${scaled_checkin:.2f}" + (" ⚠️ capped" if cap_note else "")
        })

    df_priority = pd.DataFrame(table_data)
    st.dataframe(df_priority, use_container_width=True, hide_index=True)

    if len(available_slots) > st.session_state.display_limit:
        if st.button("➕ Load 15 More Slots"):
            st.session_state.display_limit += 15
            st.rerun()

# ------------------------------------------
# TAB 2: PRE-PLANNED EXECUTION CARDS
# ------------------------------------------
elif st.session_state.active_tab == "📋 Pre-Planned Execution Cards":
    st.subheader("Pre-Planned Per-Slot Execution Cards")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        card_family = st.selectbox("1. Select Slot Family:", options=list(SLOT_MASTER_LIST.keys()), key="card_fam_select")
    with col_c2:
        card_slot = st.selectbox("2. Select Slot Theme:", options=SLOT_MASTER_LIST[card_family], key="card_slot_select")

    if card_slot:
        slot_data = next((s for s in st.session_state.slots_db if s["slot"] == card_slot and s["family"] == card_family), None)
        if slot_data:
            rehit = slot_data.get("rehit_metrics", {})
            avg_att2 = rehit.get("avg_attempt2_spins", 0)
            multi_rate = rehit.get("multi_hit_rate", 0)
            att2_pop = rehit.get("attempt2_population", 0)

            scaled_phases, scaled_checkin, posture_note, cap_note = scale_phases_for_bankroll(
                slot_data.get("phases", []), slot_data.get("checkin_alloc", 0.0)
            )

            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data.get('slot', 'N/A')}** ({slot_data.get('family', 'N/A')})")
            st.caption(f"Staking posture: {posture_note}" + (f" {cap_note}" if cap_note else ""))

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Dynamic Evaluation Window", f"{slot_data.get('total_spins', 0)} Spins", delta=f"Check-In: ${scaled_checkin:.2f}")
            col_m2.metric(f"Day Context RVI ({st.session_state.selected_day})", f"{slot_data.get('base_rvi', 0)}", delta=f"Day Weighting: {slot_data.get('day_factor', 1.0)}x")
            col_m3.metric("Sheet Repeat-Hit Rate", f"{multi_rate}%", delta=f"of {att2_pop} 2nd attempts")

            st.caption(f"**Data Proof:** {slot_data.get('source_proof', 'N/A')} | **2nd-Attempt Repeat Logs:** {rehit.get('multi_hit_count', 0)} of {att2_pop}")

            st.markdown("#### 🔄 Bankroll-Scaled Phase Plan (Attempt 1 / Hit 0)")
            for idx, phase in enumerate(scaled_phases, 1):
                st.write(f"**Phase {idx}:** **{phase.get('spins', 0)} Spins** @ **${phase.get('bet', 0):.2f}/spin** — *{phase.get('note', '')}*")

            st.markdown("---")

            st.markdown("#### 🎯 Post-Hit Repeat Execution Protocol (Attempt 2 Calibration)")
            st.info(f"📋 **Live Sheet Recommendation:** {rehit.get('repeat_recommendation', 'No data available.')}")

            col_h1, col_h2 = st.columns(2)
            p1_bet = scaled_phases[0]["bet"] if scaled_phases else 0.0

            with col_h1:
                st.markdown("##### 📊 Historical Sheet Stats (Attempt 2 population)")
                st.write(f"- **2nd Attempts Logged:** {att2_pop}")
                st.write(f"- **Multi-Hit Occurrences:** {rehit.get('multi_hit_count', 0)} times")
                st.write(f"- **Attempt 2 Avg Trigger Spin:** {avg_att2} spins" if avg_att2 > 0 else "- **Attempt 2 Avg Trigger Spin:** No Attempt 2 hits logged")
                st.write(f"- **Highest Recorded Repeat Multiplier:** {rehit.get('max_repeat_multiplier', 0)}x")
                st.write(f"- **Average Repeat Multiplier:** {rehit.get('avg_repeat_multiplier', 0)}x")

            with col_h2:
                st.markdown("##### ⚙️ Action Protocol on Feature Trigger")
                if multi_rate >= 30.0:
                    st.success("🟢 **ACTION: RESET & RE-PROBE**")
                    st.write(f"- **Execution:** Reset immediately back to **Phase 1 ({scaled_phases[0]['spins'] if scaled_phases else 0} Spins @ ${p1_bet:.2f})**.")
                    st.write(f"- **Reason:** Of {att2_pop} logged 2nd attempts, {multi_rate}% hit again.")
                else:
                    st.warning("🟡 **ACTION: FINISH CURRENT PHASE OR EXIT**")
                    st.write(f"- **Execution:** Finish only remaining spins in current phase, lock profits, and move to Played Basket.")
                    st.write(f"- **Reason:** Of {att2_pop} logged 2nd attempts, only {multi_rate}% hit again.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played (Move to Basket)"):
                res = mark_slot_played(slot_data['slot'])
                st.success(res)
                st.rerun()

# ------------------------------------------
# TAB 3: LIVE DATA ENTRY
# ------------------------------------------
elif st.session_state.active_tab == "📝 Live Data Entry":
    st.subheader("📝 Live Session Data Entry")

    chosen_date = st.date_input("Select Date:", value=datetime.now().date(), key="live_date_picker")
    dynamic_day = chosen_date.strftime("%A")
    formatted_date_str = f"{chosen_date.month}/{chosen_date.day}/{chosen_date.year}"

    st.info(f"📆 Selected Date: **{formatted_date_str}** | Day: **{dynamic_day}**")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        entry_family = st.selectbox("Slot Family:", list(SLOT_MASTER_LIST.keys()), key="live_fam_select")
    with col_f2:
        entry_slot = st.selectbox("Slot Theme Name:", SLOT_MASTER_LIST[entry_family], key="live_slot_select")

    with st.form("dynamic_gs_entry_form", clear_on_submit=True):
        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            entry_spin_hit_raw = st.text_input("Spin of Feature Hit:", value="15")
            entry_feat_type = st.selectbox("Feature Type:", ["orb", "scatter", "scatter+orb", "na"])
        with col_e2:
            entry_win_amt = st.number_input("Win Amount ($):", min_value=0, value=916, step=10)
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0.0, value=183.0, step=0.5, format="%.1f")
        with col_e3:
            entry_hit_num = st.number_input("Hit Number:", min_value=0, max_value=20, value=1)
            entry_attempt_num = st.number_input("Attempt Number:", min_value=1, max_value=20, value=1)

        submit_gs_entry = st.form_submit_button("💾 Save Record to Google Sheets")

        if submit_gs_entry:
            new_record = {
                "Date": str(formatted_date_str),
                "Day": str(dynamic_day),
                "Family": str(entry_family),
                "Slot": str(entry_slot),
                "Spin of feature hit": str(entry_spin_hit_raw.strip()),
                "Feature type": str(entry_feat_type),
                "Win amount": str(entry_win_amt),
                "Win multiplier": str(entry_multiplier),
                "Hit Number": str(entry_hit_num),
                "Attempt Number": str(entry_attempt_num)
            }

            try:
                existing_df, existing_cols = load_and_inspect_sheet()

                # Duplicate-submit guard: same slot/date/spin/attempt/hit
                # already present is very likely an accidental double-tap
                # (common on a slow mobile connection) rather than a
                # genuine second identical event.
                is_duplicate = False
                if not existing_df.empty:
                    check_cols = ["Date", "Family", "Slot", "Spin of feature hit", "Hit Number", "Attempt Number"]
                    if all(c in existing_df.columns for c in check_cols):
                        dup_mask = pd.Series(True, index=existing_df.index)
                        for c in check_cols:
                            dup_mask &= existing_df[c].astype(str).str.strip() == str(new_record.get(c, "")).strip()
                        is_duplicate = dup_mask.any()

                if is_duplicate:
                    st.warning("⚠️ This looks like a duplicate of a row already in the sheet (same date, slot, spin, hit #, attempt #). Not saved — resubmit only if this is genuinely a repeat entry you intend to add.")
                else:
                    new_row_df = pd.DataFrame([new_record])
                    if not existing_df.empty:
                        for col in existing_cols:
                            if col not in new_row_df.columns:
                                new_row_df[col] = ""
                        updated_df = pd.concat([existing_df.astype(str), new_row_df.astype(str)], ignore_index=True)
                    else:
                        updated_df = new_row_df.astype(str)

                    conn.update(worksheet=SESSION_LOG_WORKSHEET, data=updated_df)
                    mark_slot_played(entry_slot)
                    st.cache_data.clear()
                    st.success(f"✅ Recorded '{entry_slot}' ({entry_family}) on {dynamic_day}! Matrix recalculated.")
                    st.rerun()
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# ------------------------------------------
# TAB 4: INTERACTIVE AI AGENT
# ------------------------------------------
elif st.session_state.active_tab == "🤖 Interactive AI Agent":
    st.subheader("🤖 Live Strategy AI Agent (Gemini primary, Groq fallback)")

    # Display prior chat history
    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Quick action prompt chips
    col_q1, col_q2, col_q3 = st.columns(3)
    prompt_to_submit = None

    if col_q1.button("🎯 Top 3 Best Slots Today"):
        prompt_to_submit = f"What are the top 3 best slots to play today ({st.session_state.selected_day}) based on our Day-RVI matrix?"
    if col_q2.button("💰 Check Session Bankroll Status"):
        prompt_to_submit = f"Assess my session status. Starting bankroll was ${st.session_state.session_start_bankroll}, current is ${st.session_state.current_bankroll}, and target is ${st.session_state.session_target}."
    if col_q3.button("🔄 Check High Multi-Hit Machines"):
        prompt_to_submit = "Which slots currently have the highest repeat-hit rate (>30% of 2nd attempts) and should be re-probed immediately after a feature win?"

    user_input = st.chat_input("Ask your AI Execution Agent anything about today's session strategy...")
    if user_input:
        prompt_to_submit = user_input

    if prompt_to_submit:
        st.session_state.chat_messages.append({"role": "user", "content": prompt_to_submit})
        with st.chat_message("user"):
            st.markdown(prompt_to_submit)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing live matrix & generating strategic plan..."):
                st.session_state.pending_rerun = False
                response_text, provider = run_ai_agent(prompt_to_submit)
                st.caption(f"_Answered via {provider}_")
                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})

        if st.session_state.get("pending_rerun"):
            st.session_state.pending_rerun = False
            st.rerun()

# ------------------------------------------
# TAB 5: PLAYED BASKET & OVERRIDES
# ------------------------------------------
elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
    st.subheader("🧺 Played Basket & Active Session Overrides")

    if not st.session_state.played_basket:
        st.info("No machines have been marked as played yet today.")
    else:
        st.write("The following machines are currently marked as played and excluded from top active priority lists:")
        for slot in st.session_state.played_basket:
            col_p1, col_p2 = st.columns([3, 1])
            with col_p1:
                st.write(f"• **{slot}**")
            with col_p2:
                if st.button("Restore to Active List", key=f"restore_{slot}"):
                    restore_slot(slot)
                    st.rerun()

        st.markdown("---")
        if st.button("🗑️ Clear Entire Played Basket"):
            st.session_state.played_basket = []
            persist_session_state()
            st.rerun()
