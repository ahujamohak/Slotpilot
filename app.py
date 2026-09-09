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

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-120b"
SESSION_STATE_WORKSHEET = "Live Session"
SESSION_LOG_WORKSHEET = "Session Log"

TAB_OPTIONS = [
    "📊 Today's Priority Board",
    "📈 Overall Performance",
    "📋 Pre-Planned Execution Cards",
    "📝 Live Data Entry",
    "🤖 Interactive AI Agent",
    "🧺 Played Basket & Overrides"
]

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
    st.session_state.active_tab = "📊 Today's Priority Board"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []
    st.session_state.selected_day = datetime.now().strftime("%A")
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
        "repeat_sample_size": 0,
        "attempt2_population": 0,
        "multi_hit_count": 0,
        "multi_hit_rate": 0.0,
        "avg_repeat_multiplier": 0.0,
        "max_repeat_multiplier": 0.0,
        "avg_attempt2_spins": 0.0,
        "repeat_recommendation": "No Repeat Data",
        "first_hit_count": 0,
        "first_hit_total": 0,
        "avg_first_multiplier": 0.0,
        "avg_first_spins": 0.0,
    }

    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return default_res

    total_logs = len(parsed_df)

    for col in ["_feature_win_num", "_hit", "_attempt", "_mult", "_spins"]:
        if col in parsed_df.columns:
            parsed_df[col] = pd.to_numeric(parsed_df[col], errors="coerce")

    # Dynamic Filter for 1st Feature Hits
    first_hits = parsed_df[
        (parsed_df["_feature_win_num"] == 1) & (parsed_df["_spins"].notna())
    ]
    
    if first_hits.empty:
        first_hits = parsed_df[
            (parsed_df["_hit"] == 1) & (parsed_df["_attempt"] == 1) & (parsed_df["_spins"].notna())
        ]

    first_hit_count = len(first_hits)
    avg_first_mult = round(float(first_hits["_mult"].mean()), 1) if not first_hits.empty else 0.0
    
    # Calculate Average Spin Count across valid 1st hits
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

    if attempt2_population == 0 and repeat_count == 0:
        recommendation = "ℹ️ UNTESTED REPEAT PROFILE: No second feature logged yet."
    elif multi_hit_rate >= 40.0:
        recommendation = f"🔥 HIGH REPEAT POTENTIAL ({multi_hit_rate}%): Re-probe strategy immediately after win."
    elif multi_hit_rate >= 20.0:
        recommendation = f"⚡ MODERATE REPEAT POTENTIAL ({multi_hit_rate}%): Re-probe if win > 20x."
    else:
        recommendation = f"⚠️ LOW REPEAT POTENTIAL ({multi_hit_rate}%): Single hit machine. Lock profits and exit."

    return {
        "repeat_sample_size": total_logs,
        "attempt2_population": attempt2_population,
        "multi_hit_count": repeat_count,
        "multi_hit_rate": multi_hit_rate,
        "avg_repeat_multiplier": avg_repeat_mult,
        "max_repeat_multiplier": max_repeat_mult,
        "avg_attempt2_spins": avg_att2_spins,
        "repeat_recommendation": recommendation,
        "first_hit_count": first_hit_count,
        "first_hit_total": total_logs,
        "avg_first_multiplier": avg_first_mult,
        "avg_first_spins": avg_first_spins,
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

    def _rank_key(x):
        total = x.get("total_hits", 0) or 0
        sample_bonus = min(total, 20) / 20.0
        reliability = 1.0 if total >= 5 else 0.3
        return (
            x["rvi"] * reliability,
            x["rehit_metrics"].get("multi_hit_rate", 0),
            sample_bonus,
            x.get("day_hits", 0),
        )

    slot_scores = sorted(slot_scores, key=_rank_key, reverse=True)

    for item in slot_scores:
        records.append({
            "family": item["family"],
            "slot": item["slot"],
            "base_rvi": item["rvi"],
            "checkin_alloc": 500.0,
            "strategy_plan": STRATEGY_PLAN_SUMMARY,
            "source_proof": item["source_proof"],
            "target_day": item["target_day"],
            "day_factor": item["day_factor"],
            "day_hits": item["day_hits"],
            "total_hits": item["total_hits"],
            "rehit_metrics": item["rehit_metrics"]
        })
    return sorted(records, key=lambda x: (x["base_rvi"], x["rehit_metrics"]["multi_hit_rate"]), reverse=True)

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
            "slot": s["slot"],
            "family": s["family"],
            "rvi_score": s["base_rvi"],
            "multi_hit_rate": f"{s['rehit_metrics']['multi_hit_rate']}%",
            "multi_hit_count": s['rehit_metrics']['multi_hit_count'],
            "attempt2_population": s['rehit_metrics'].get('attempt2_population', 0),
            "strategy_plan": STRATEGY_PLAN_SUMMARY,
            "checkin_alloc": "$500",
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
    - Fixed Bet Denom Rotation: Always $5.00 bet per spin. Rotate through 5 denoms ($1.00, $0.10, $0.05, $0.02, $0.01).
    - Dynamic Spin Count: Consuming $100 per denom results in 20 base spins, but line wins (small to big) re-fund play, resulting in 20 to 50+ spins per denom.
    - Exit Criteria: Stop on a denom when its $100 allocation is consumed or shift to next denom. If feature hits, book profit at $700+ balance ($200 profit), or exit if balance drops back to $500.

    AVAILABLE TOP-RANKED SLOTS DATASET:
    {slot_context_summary}

    OPERATIONAL INSTRUCTIONS:
    1. Advise the user based strictly on the $500 check-in, fixed $5 bet denomination cycle ($100 allocated per denom), and line win dynamics.
    2. Acknowledge that spin counts per denom vary (20-50+ spins) based on line wins, but the bankroll budget ($100/denom) remains fixed.
    3. You have tool function calls to mark machines played or update bankroll directly.
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
        return "⚠️ Groq fallback unavailable: `GROQ_API_KEY` is not set."

    system_instruction = build_agent_context() + "\n\nNOTE: Text-only fallback mode active."
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
with st.sidebar.form("quick_mark_played_form"):
    qm_family = st.selectbox("Family:", options=list(SLOT_MASTER_LIST.keys()), key="qm_fam")
    qm_slot = st.selectbox("Slot:", options=SLOT_MASTER_LIST[qm_family], key="qm_slot")
    qm_submit = st.form_submit_button("Mark as Played", use_container_width=True)
    if qm_submit:
        res = mark_slot_played(qm_slot)
        st.sidebar.success(res)
        st.rerun()

# ==========================================
# 5. DASHBOARD VIEWS
# ==========================================

# TAB 1: TODAY'S PRIORITY BOARD
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board")

    filtered_slots = []
    for s in st.session_state.slots_db:
        if s["slot"] in st.session_state.played_basket:
            continue

        rehit = s.get("rehit_metrics", {})
        first_total = rehit.get("first_hit_total", 0)

        # Filter 1: Total attempts in the first hit must be > 5
        if first_total > 5:
            first_hits = rehit.get("first_hit_count", 0)
            success_rate = (first_hits / first_total) if first_total > 0 else 0.0
            
            s_copy = dict(s)
            s_copy["_calc_success_rate"] = success_rate
            filtered_slots.append(s_copy)

    # Filter 2: Sort based on success rate from highest to lowest
    sorted_slots = sorted(filtered_slots, key=lambda x: x["_calc_success_rate"], reverse=True)
    current_display = sorted_slots[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        rehit = item.get("rehit_metrics", {})
        att2_pop = rehit.get("attempt2_population", 0)
        first_hits = rehit.get("first_hit_count", 0)
        first_total = rehit.get("first_hit_total", 0)
        avg_1st_mult = rehit.get("avg_first_multiplier", 0.0)
        avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0)
        avg_spins = rehit.get("avg_first_spins", 0.0)

        success_pct = f"{round(item['_calc_success_rate'] * 100, 1)}%"

        table_data.append({
            "Rank": rank,
            "Slot Theme": item.get("slot", "N/A"),
            "Family": item.get("family", "N/A"),
            "Average Spin Count": f"{avg_spins}" if avg_spins > 0 else "N/A",
            "1st Hits/Total": f"{first_hits} / {first_total} ({success_pct})",
            "Avg 1st Mult": f"{avg_1st_mult}x" if avg_1st_mult > 0 else "N/A",
            "2nd Hits/Total": f"{rehit.get('multi_hit_count', 0)} / {att2_pop}",
            "Avg 2nd Mult": f"{avg_2nd_mult}x" if avg_2nd_mult > 0 else "N/A",
        })

    df_priority = pd.DataFrame(table_data)

    if df_priority.empty:
        st.info("No slots with > 5 total attempts available for today's filter.")
    else:
        st.dataframe(
            df_priority,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", width="small"),
                "Slot Theme": st.column_config.TextColumn("Slot Theme", width="medium"),
                "Family": st.column_config.TextColumn("Family", width="medium"),
                "Average Spin Count": st.column_config.TextColumn("Average Spin Count", width="small"),
                "1st Hits/Total": st.column_config.TextColumn("1st Hits/Total", width="medium"),
                "Avg 1st Mult": st.column_config.TextColumn("Avg 1st Mult", width="small"),
                "2nd Hits/Total": st.column_config.TextColumn("2nd Hits/Total", width="small"),
                "Avg 2nd Mult": st.column_config.TextColumn("Avg 2nd Mult", width="small"),
            }
        )

    if len(sorted_slots) > st.session_state.display_limit:
        if st.button("➕ Load 15 More Slots"):
            st.session_state.display_limit += 15
            st.rerun()

# TAB 2: OVERALL PERFORMANCE
elif st.session_state.active_tab == "📈 Overall Performance":
    st.subheader("📈 Overall Performance (All Historical Logs)")
    st.caption("Calculated across your entire dataset regardless of target day penalties or specific day filtering.")

    overall_slots = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rehit = compute_slot_rehit_metrics(slot, fam, live_sheet_df)
            first_total = rehit.get("first_hit_total", 0)
            if first_total > 5:
                first_hits = rehit.get("first_hit_count", 0)
                success_rate = (first_hits / first_total) if first_total > 0 else 0.0
                overall_slots.append({
                    "family": fam,
                    "slot": slot,
                    "calc_success_rate": success_rate,
                    "rehit_metrics": rehit
                })

    sorted_overall = sorted(overall_slots, key=lambda x: x["calc_success_rate"], reverse=True)

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
        st.info("No slots with > 5 total attempts found in historical logs.")
    else:
        st.dataframe(
            df_overall,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", width="small"),
                "Slot Theme": st.column_config.TextColumn("Slot Theme", width="medium"),
                "Family": st.column_config.TextColumn("Family", width="medium"),
                "Average Spin Count": st.column_config.TextColumn("Average Spin Count", width="small"),
                "1st Hits/Total": st.column_config.TextColumn("1st Hits/Total", width="medium"),
                "Avg 1st Mult": st.column_config.TextColumn("Avg 1st Mult", width="small"),
                "2nd Hits/Total": st.column_config.TextColumn("2nd Hits/Total", width="small"),
                "Avg 2nd Mult": st.column_config.TextColumn("Avg 2nd Mult", width="small"),
            }
        )

# TAB 3: PRE-PLANNED EXECUTION CARDS
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
            multi_rate = rehit.get("multi_hit_rate", 0)
            att2_pop = rehit.get("attempt2_population", 0)

            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data.get('slot', 'N/A')}** ({slot_data.get('family', 'N/A')})")

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Check-In Budget", "$500 Total", delta="$100 / Denom @ $5 Bet")
            col_m2.metric(f"Day Context RVI ({st.session_state.selected_day})", f"{slot_data.get('base_rvi', 0)}", delta=f"{slot_data.get('day_factor', 1.0)}x Weight")
            col_m3.metric("Repeat Hit Rate", f"{multi_rate}%", delta=f"of {att2_pop} 2nd attempts")

            st.markdown("#### 💵 Fixed $5 Bet Denomination Breakdown ($500 Total Budget)")
            
            df_steps = pd.DataFrame(STRATEGY_STEPS)[["step", "denom", "bet", "budget", "spins"]]
            df_steps.columns = ["Step #", "Denomination", "Fixed Bet / Spin ($)", "Allocated Budget ($)", "Est. Spins (Line Wins Dynamic)"]
            
            st.dataframe(df_steps, use_container_width=True, hide_index=True)

            st.markdown("#### 🚨 Lock-in & Profit Exit Rules")
            st.info("""
            * **0 Feature Hits:** Walk off after consuming the $500 total budget across all 5 denoms (spins will range from 100 up to 200+ depending on line win frequency).
            * **Hit Feature:**
              * **Target Hit ($700+ balance):** Cash out & book $200+ profit immediately.
              * **Fall Back ($500 balance):** If balance drops back to $500, exit immediately (Break-Even).
              * **In-Between ($500 - $700):** Continue probing until reaching $700 or falling back to $500.
            """)

            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
                res = mark_slot_played(slot_data['slot'])
                st.success(res)
                st.rerun()

# TAB 4: LIVE DATA ENTRY
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
            entry_feat_win_num = st.number_input("Feature Win Number:", min_value=0, max_value=20, value=1)

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
                "Attempt Number": str(entry_attempt_num),
                "Feature Win Number": str(entry_feat_win_num)
            }

            try:
                existing_df, existing_cols = load_and_inspect_sheet()
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
                st.success(f"✅ Recorded '{entry_slot}'! Matrix recalculated.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# TAB 5: INTERACTIVE AI AGENT
elif st.session_state.active_tab == "🤖 Interactive AI Agent":
    st.subheader("🤖 Slotpilot AI Assistant")

    # Header Metric Context Card
    with st.container():
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Active Day Focus", st.session_state.selected_day)
        m_col2.metric("Current Bankroll", f"${st.session_state.current_bankroll:.2f}")
        m_col3.metric("Target Goal", f"${st.session_state.session_target:.2f}")
        m_col4.metric("Played Today", f"{len(st.session_state.played_basket)} Machines")

    st.markdown("---")
    st.markdown("#### ⚡ Quick Actions & Session Insights")

    prompt_to_submit = None

    # Card Grid layout for quick questions
    q_col1, q_col2, q_col3 = st.columns(3)
    with q_col1:
        if st.button("🏆 **Top Priority Recommendation**\n\nShow me the top 3 best slots for today.", use_container_width=True):
            prompt_to_submit = f"What are the top 3 best slots to play today ({st.session_state.selected_day}) based on our Day-RVI matrix?"
    with q_col2:
        if st.button("🔥 **High Repeat Multipliers**\n\nFind slots with repeat-hit rates >30%.", use_container_width=True):
            prompt_to_submit = "Which slots currently have the highest repeat-hit rate (>30% of 2nd attempts)?"
    with q_col3:
        if st.button("💵 **Bankroll Strategy Check**\n\nHow should I budget my active bankroll?", use_container_width=True):
            prompt_to_submit = f"Given my current bankroll of ${st.session_state.current_bankroll:.2f}, guide my next play sequence."

    st.markdown("---")

    # Chat history display container
    chat_container = st.container()
    with chat_container:
        for message in st.session_state.chat_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    user_input = st.chat_input("Ask your Slotpilot AI Execution Agent anything...")
    if user_input:
        prompt_to_submit = user_input

    if prompt_to_submit:
        st.session_state.chat_messages.append({"role": "user", "content": prompt_to_submit})
        with st.chat_message("user"):
            st.markdown(prompt_to_submit)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing live session matrix..."):
                response_text, provider = run_ai_agent(prompt_to_submit)
                st.caption(f"_Source: Slotpilot Engine ({provider})_")
                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})

        if st.session_state.get("pending_rerun"):
            st.session_state.pending_rerun = False
            st.rerun()

# TAB 6: PLAYED BASKET & OVERRIDES
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
