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

# ==========================================
# 0. PAGE CONFIG & CONNECTION MANAGEMENT
# ==========================================

st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

# Google Sheets Connection
conn = st.connection("gsheets", type=GSheetsConnection)

TAB_OPTIONS = [
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "📝 Live Data Entry",
    "🤖 Interactive AI Agent",
    "🧺 Played Basket & Overrides"
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "📊 Today's Priority Board"

def reset_all_state():
    st.session_state.played_basket = []
    st.session_state.display_limit = 30
    st.session_state.session_start_bankroll = 1000.0
    st.session_state.current_bankroll = 1000.0
    st.session_state.session_target = 1800.0
    st.session_state.active_tab = "📊 Today's Priority Board"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []

if "strict_day_penalty" not in st.session_state:
    st.session_state.strict_day_penalty = True

if "played_basket" not in st.session_state:
    reset_all_state()

# ==========================================
# 1. MASTER LIST & MULTI-PHASE CONFIG
# ==========================================

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
    "Bull Blitz": ["Golden Empress", "Maximus Money", "Roses & Riches"],
    "Bull Rush": ["Fire Mountain", "Golden Empress"],
    "Bull Rush Blitz": ["Golden Empress", "Maximus Money", "Wild Outback", "Yarr Matey"],
    "Bull Rush Blitz 2 Multi": ["Maximus Money", "New York Nights", "Roses & Riches"],
    "Bull Rush Blitz 3 Multi": ["El Metador"],
    "Bull Rush Stampede": ["Fire Mountain", "Maximus Money", "Minotaur’s Treasure"],
    "Cash Horns": ["Cleopatra’s Kingdom", "Grand Toro", "Master Warrior", "Ragnar the Great"],
    "Cash Spark": ["Royal Spark"],
    "Choy's Kingdom": ["Lunar Festival"],
    "Dollar Storm": ["Aussie Boomer", "Caribbean Gold", "Egyptian Jewels", "Fight for Troy", "Ninja Moon"],
    "Dragon Cash": ["Genghis Khan", "Magic Panda"],
    "Dragon Link": ["Autumn Moon", "Genghis Khan", "Golden Century", "Golden Gong", "Happy & Prosperous", "Panda Magic", "Peace & Long Life", "Peacock Princess", "Silk Road", "Spring Festival"],
    "Dragon Rush": ["Battle Drum", "Shadow Clan", "Shaolin Style"],
    "Dragon Train": ["Chillin Wins", "Forever Emperor", "Khutulun Battle Princess", "Sun Shots"],
    "Dragon Train Link": ["Forever Emperor", "Sun Shots"],
    "Eureka n more blastin": ["Eureka n more blastin"],
    "Fabulous Hold & Spin Jackpot": ["Cash Champ", "Come one, Come all", "Glitter & Glitz", "Magic Touch"],
    "Fireball": ["Sea Queen Express", "Shogun Express"],
    "Fortune Hearts": ["Emperor's Choice", "Fire Spell", "Lunar Dragon"],
    "Go for Grand": ["Golden Sombreros", "Outback Gold", "Power Charms"],
    "Golden Strike": ["Viking Vallhala"],
    "Grand Legends": ["Great King", "Magic Warrior", "Royal Emperor", "Sun Queen"],
    "Heaven & Earth": ["Lucky Pig", "Shaolin Ways", "Terracotta Emperor"],
    "Huff 'N' Even More Puff": ["Huff n Even More Puff"],
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
    "Thunder": ["Fire Legend", "Inca Diamonds"],
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
        df = conn.read(worksheet="Session Log", ttl="0")
        if df.empty:
            return pd.DataFrame(), []
        df.columns = [str(c).strip() for c in df.columns]
        return df, list(df.columns)
    except Exception:
        return pd.DataFrame(), []

def compute_slot_rehit_metrics(slot_name, family_name, live_df):
    default_res = {
        "repeat_sample_size": 0,
        "multi_hit_count": 0,
        "multi_hit_rate": 0.0,
        "avg_repeat_multiplier": 0.0,
        "max_repeat_multiplier": 0.0,
        "repeat_recommendation": "No Repeat Data (Follow Baseline Probe)"
    }
    if live_df.empty:
        return default_res

    cols = {str(c).lower(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    fam_col = cols.get("family") or cols.get("slot family")
    attempt_col = cols.get("attempt number") or cols.get("attempt")
    hit_num_col = cols.get("hit number") or cols.get("hit")
    mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")

    matched = live_df.copy()
    if slot_col and slot_col in matched.columns:
        matched = matched[matched[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]
    elif fam_col and fam_col in matched.columns:
        matched = matched[matched[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower()]

    if matched.empty:
        return default_res

    matched["_attempt"] = pd.to_numeric(matched[attempt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(1) if attempt_col else 1
    matched["_hit"] = pd.to_numeric(matched[hit_num_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0) if hit_num_col else 0
    matched["_mult"] = pd.to_numeric(matched[mult_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0) if mult_col else 0

    repeat_entries = matched[(matched["_attempt"] >= 2) | (matched["_hit"] >= 2)]
    total_logs = len(matched)
    repeat_count = len(repeat_entries)

    if total_logs == 0:
        return default_res

    multi_hit_rate = round((repeat_count / total_logs) * 100.0, 1)
    avg_repeat_mult = round(repeat_entries["_mult"].mean(), 1) if not repeat_entries.empty else 0.0
    max_repeat_mult = round(repeat_entries["_mult"].max(), 1) if not repeat_entries.empty else 0.0

    if multi_hit_rate >= 40.0:
        recommendation = f"🔥 HIGH REPEAT POTENTIAL ({multi_hit_rate}% Multi-Hit Rate): Reset to Phase 1 immediately after feature hit."
    elif multi_hit_rate >= 20.0:
        recommendation = f"⚡ MODERATE REPEAT POTENTIAL ({multi_hit_rate}% Multi-Hit Rate): Finish current phase; re-probe if win > 20x."
    elif repeat_count > 0:
        recommendation = f"⚠️ LOW REPEAT POTENTIAL ({multi_hit_rate}% Multi-Hit Rate): Single hit machine. Lock profits and exit."
    else:
        recommendation = "ℹ️ UNTESTED REPEAT PROFILE: No multi-attempt/hit data recorded yet."

    return {
        "repeat_sample_size": total_logs,
        "multi_hit_count": repeat_count,
        "multi_hit_rate": multi_hit_rate,
        "avg_repeat_multiplier": avg_repeat_mult,
        "max_repeat_multiplier": max_repeat_mult,
        "repeat_recommendation": recommendation
    }

def compute_75_25_rvi(slot_name, family_name, live_df, target_day=None, strict_mode=True):
    baseline_score = 7.5
    if target_day is None:
        target_day = datetime.now().strftime("%A")

    if live_df.empty:
        return baseline_score, "100% Baseline (No Data)", target_day, 1.0, 0, 0

    cols = {str(c).lower(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    fam_col = cols.get("family") or cols.get("slot family") or cols.get("family name")
    day_col = cols.get("day") or cols.get("day of week")
    win_mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    win_amt_col = cols.get("win amount") or cols.get("win ($)")

    matched_rows = live_df.copy()
    if slot_col and slot_col in matched_rows.columns:
        matched_rows = matched_rows[matched_rows[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]

    if fam_col and fam_col in matched_rows.columns and not matched_rows.empty:
        fam_matched = matched_rows[matched_rows[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower()]
        if not fam_matched.empty:
            matched_rows = fam_matched
        else:
            return baseline_score, "25% Baseline / 0 Logs for Family", target_day, 1.0, 0, 0

    if matched_rows.empty:
        return baseline_score, "25% Baseline / 0 Logs", target_day, 1.0, 0, 0

    total_logs = len(matched_rows)
    day_log_count = 0
    day_factor = 1.0

    if day_col and day_col in matched_rows.columns:
        day_matches = matched_rows[matched_rows[day_col].astype(str).str.strip().str.lower() == str(target_day).strip().lower()]
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

    empirical_multipliers = []
    if win_mult_col and win_mult_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_mult_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0).tolist()
    elif win_amt_col and win_amt_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_amt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').fillna(0).tolist()

    if not empirical_multipliers:
        final_rvi = round(baseline_score * day_factor, 2)
        return final_rvi, f"Day-Weighted Hybrid ({day_log_count} {target_day} hits)", target_day, day_factor, day_log_count, total_logs

    # --- RVI CALCULATION BUG FIX: SEPARATING HIT RATE FROM WIN MAGNITUDE ---
    # Filter only positive win events (> 0 multiplier) to derive actual win magnitude given a hit
    actual_hits = [m for m in empirical_multipliers if m > 0]
    
    # 1. Hit Rate Signal (Frequency of feature trigger)
    hit_rate = len(actual_hits) / total_logs if total_logs > 0 else 0.0
    hit_rate_score = min(10.0, max(1.0, hit_rate * 10.0))  # Scale 0-100% hit rate into 1.0 - 10.0 score

    # 2. Win Magnitude Signal (Average payout when feature hits)
    if len(actual_hits) > 0:
        avg_win_when_hit = np.mean(actual_hits)
        win_magnitude_score = min(10.0, max(1.0, (avg_win_when_hit / 15.0) + 5.0))
    else:
        win_magnitude_score = 1.0

    # Combine signals independently: 40% Hit Frequency, 60% Win Magnitude
    sheet_rvi = (0.40 * hit_rate_score) + (0.60 * win_magnitude_score)
    
    # Final 75/25 Hybrid Blending with baseline & Day Factor
    weighted_rvi = (0.75 * sheet_rvi) + (0.25 * baseline_score)
    final_rvi = round(min(10.0, max(1.0, weighted_rvi * day_factor)), 2)
    proof_str = f"75% Live Sheet ({len(actual_hits)}/{total_logs} hits, {day_log_count} on {target_day}s)"
    
    return final_rvi, proof_str, target_day, day_factor, day_log_count, total_logs

def get_multi_phase_execution(slot_name, rvi_score):
    if slot_name in CUSTOM_HIT_ZONES:
        phases = CUSTOM_HIT_ZONES[slot_name]["phases"]
    else:
        if rvi_score >= 8.5:
            phases = [
                {"spins": 15, "bet": 7.50, "note": "Initial Probe"},
                {"spins": 15, "bet": 10.00, "note": "High Hit Zone"},
                {"spins": 15, "bet": 5.00, "note": "Mid Checkpoint"},
                {"spins": 15, "bet": 7.50, "note": "Late Expansion"}
            ]
        elif rvi_score >= 7.0:
            phases = [
                {"spins": 10, "bet": 3.75, "note": "Probe Phase"},
                {"spins": 15, "bet": 5.00, "note": "Target Zone"},
                {"spins": 15, "bet": 2.50, "note": "Step Down"},
                {"spins": 10, "bet": 3.75, "note": "Final Check"}
            ]
        else:
            phases = [
                {"spins": 10, "bet": 2.50, "note": "Probe Phase"},
                {"spins": 15, "bet": 3.75, "note": "Evaluation"},
                {"spins": 10, "bet": 1.25, "note": "Exit Check"}
            ]

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

        phases, total_spins, checkin_alloc = get_multi_phase_execution(slot, rvi_score)
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

live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()

if "selected_day" not in st.session_state:
    st.session_state.selected_day = datetime.now().strftime("%A")

st.session_state.slots_db = build_priority_dataset(live_sheet_df, st.session_state.selected_day, st.session_state.strict_day_penalty)

def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)
        return f"Successfully marked '{slot_name}' as played."
    return f"'{slot_name}' is already in the played basket."

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)
        return f"Restored '{slot_name}' to active status."
    return f"'{slot_name}' was not found in played basket."
    
# ==========================================
# 3. GEMINI 3.6 LIVE AGENT ENGINE & TOOLS
# ==========================================

@st.cache_resource
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

def tool_mark_machine_played(slot_name: str) -> str:
    """Marks a machine as played, moving it out of active recommendations and into the played basket."""
    res = mark_slot_played(slot_name)
    st.rerun()
    return res

def tool_update_bankroll(new_amount: float) -> str:
    """Updates the user's current bankroll during a live casino session."""
    st.session_state.current_bankroll = float(new_amount)
    return f"Current bankroll updated to ${new_amount:.2f}"

def run_gemini_agent(user_prompt: str):
    client = get_gemini_client()
    if not client:
        return "⚠️ Gemini Client error: `GEMINI_API_KEY` is not set in environment or Streamlit secrets."

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]

    slot_context_summary = []
    for s in available_slots[:20]:
        slot_context_summary.append({
            "slot": s["slot"],
            "family": s["family"],
            "rvi_score": s["base_rvi"],
            "multi_hit_rate": f"{s['rehit_metrics']['multi_hit_rate']}%",
            "multi_hit_count": s['rehit_metrics']['multi_hit_count'],
            "total_logs": s['rehit_metrics']['repeat_sample_size'],
            "phase_plan": s["phase_breakdown"],
            "checkin_alloc": s["checkin_alloc"],
            "recommendation_protocol": s['rehit_metrics']['repeat_recommendation']
        })

    system_instruction = f"""
    You are an expert AI Casino Slot Optimization & Execution Agent.

    CURRENT LIVE SESSION ENVIRONMENT:
    - Active Target Day: {st.session_state.selected_day}
    - Current Active Bankroll: ${st.session_state.current_bankroll:.2f}
    - Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}
    - Target Bankroll: ${st.session_state.session_target:.2f}
    - Strict Day Penalty Mode: {'ON' if st.session_state.strict_day_penalty else 'OFF'}
    - Played Basket (Played Today): {st.session_state.played_basket}

    AVAILABLE TOP-RANKED SLOTS DATASET (Ranked by 75/25 Hybrid Day-RVI & Multi-Hit Rate):
    {slot_context_summary}

    OPERATIONAL INSTRUCTIONS:
    1. Reason through user questions dynamically using the live slot dataset provided above.
    2. When asked for N recommendations (e.g., "Suggest 3 best slots for today"), extract the top N unplayed machines from the dataset, provide their dynamic phase progression plans, and detail the post-hit repeat execution protocol.
    3. Be precise with mathematical references (Day-RVI scores, Multi-Hit Rates, spin counts, and bet sizes).
    4. You have access to tool function calls to mark machines played or update bankrolls directly if requested by the user.
    """

    contents = []
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
    
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))

    tools_list = [tool_mark_machine_played, tool_update_bankroll]

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_list
            )
        )

        if response.function_calls:
            for fn in response.function_calls:
                if fn.name == "tool_mark_machine_played":
                    args = fn.args
                    tool_res = tool_mark_machine_played(args.get("slot_name"))
                    return f"🤖 Tool Action: {tool_res}"
                elif fn.name == "tool_update_bankroll":
                    args = fn.args
                    tool_res = tool_update_bankroll(args.get("new_amount"))
                    return f"🤖 Tool Action: {tool_res}"

        return response.text
    except Exception as e:
        return f"Error communicating with Gemini Agent: {e}"

# ==========================================
# 4. SIDEBAR & NAVIGATION
# ==========================================

st.sidebar.title("🎰 Live Session Hub")

# ONE-BUTTON GLOBAL RESET
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
selected_day_input = st.sidebar.selectbox("Filter Target Day:", options=days_list, index=current_day_idx)

strict_penalty_toggle = st.sidebar.checkbox("Strict Day Match (Penalize 0-Hit Days)", value=st.session_state.strict_day_penalty)

if selected_day_input != st.session_state.selected_day or strict_penalty_toggle != st.session_state.strict_day_penalty:
    st.session_state.selected_day = selected_day_input
    st.session_state.strict_day_penalty = strict_penalty_toggle
    st.session_state.slots_db = build_priority_dataset(live_sheet_df, st.session_state.selected_day, st.session_state.strict_day_penalty)
    st.rerun()

st.sidebar.subheader("📌 Navigation")
for tab_name in TAB_OPTIONS:
    is_active = (st.session_state.active_tab == tab_name)
    btn_type = "primary" if is_active else "secondary"
    if st.sidebar.button(tab_name, key=f"nav_btn_{tab_name}", use_container_width=True, type=btn_type):
        st.session_state.active_tab = tab_name
        st.rerun()

st.sidebar.markdown("---")
st.session_state.session_start_bankroll = st.sidebar.number_input("Starting Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
st.session_state.current_bankroll = st.sidebar.number_input("Current Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
st.session_state.session_target = st.sidebar.number_input("Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)

# ==========================================
# 5. DASHBOARD VIEWS
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")
st.caption(f"Active View: **{st.session_state.active_tab}** | Target Day Context: **{st.session_state.selected_day}** | Strict Mode: **{'ON' if st.session_state.strict_day_penalty else 'OFF'}**")
st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader(f"Today's Priority Board (Day & Repeat-Hit Weighted Matrix for {st.session_state.selected_day})")

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    current_display = available_slots[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        rehit = item["rehit_metrics"]
        table_data.append({
            "Rank": rank,
            "Slot Family": item["family"],
            "Slot Theme Name": item["slot"],
            "Day-RVI Score": item["base_rvi"],
            "Multi-Hit Rate (%)": f"{rehit['multi_hit_rate']}%",
            "Multi-Hit Hits/Total": f"{rehit['multi_hit_count']} / {rehit['repeat_sample_size']}",
            "Avg Repeat Win": f"{rehit['avg_repeat_multiplier']}x",
            "Day Factor": f"{item['day_factor']}x",
            "Proof & History": item["source_proof"],
            "Check-In Alloc ($)": f"${item['checkin_alloc']:.2f}"
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
            rehit = slot_data["rehit_metrics"]
            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")
            
            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Probe Evaluation Window", f"{slot_data['total_spins']} Spins", delta=f"Check-In: ${slot_data['checkin_alloc']:.2f}")
            col_m2.metric(f"Day Context RVI ({st.session_state.selected_day})", f"{slot_data['base_rvi']}", delta=f"Day Weighting: {slot_data['day_factor']}x")
            col_m3.metric("Sheet Multi-Hit Frequency", f"{rehit['multi_hit_rate']}%", delta=f"Avg Repeat Win: {rehit['avg_repeat_multiplier']}x")

            st.caption(f"**Data Proof:** {slot_data['source_proof']} | **Repeat Logs Analyzed (Attempt/Hit ≥ 2):** {rehit['multi_hit_count']} of {rehit['repeat_sample_size']}")

            st.markdown("#### 🔄 Dynamic Initial Probe Plan (Attempt 1 / Hit 0)")
            for idx, phase in enumerate(slot_data["phases"], 1):
                st.write(f"**Phase {idx}:** **{phase['spins']} Spins** @ **${phase['bet']:.2f}/spin** — *{phase['note']}*")

            st.markdown("---")

            st.markdown("#### 🎯 Post-Hit Repeat Execution Protocol (Driven by Sheet History)")
            st.info(f"📋 **Live Sheet Recommendation:** {rehit['repeat_recommendation']}")

            col_h1, col_h2 = st.columns(2)
            p1_bet = slot_data["phases"][0]["bet"]
            
            with col_h1:
                st.markdown("##### 📊 Historical Sheet Stats (Attempt ≥ 2 / Hit ≥ 2)")
                st.write(f"- **Multi-Hit Occurrences:** {rehit['multi_hit_count']} times")
                st.write(f"- **Highest Recorded Repeat Multiplier:** {rehit['max_repeat_multiplier']}x")
                st.write(f"- **Average Repeat Multiplier:** {rehit['avg_repeat_multiplier']}x")

            with col_h2:
                st.markdown("##### ⚙️ Action Protocol on Feature Trigger")
                if rehit["multi_hit_rate"] >= 30.0:
                    st.success("🟢 **ACTION: RESET & RE-PROBE**")
                    st.write(f"- **Execution:** Reset immediately back to **Phase 1 ({slot_data['phases'][0]['spins']} Spins @ ${p1_bet:.2f})**.")
                    st.write(f"- **Reason:** Historical data shows a strong {rehit['multi_hit_rate']}% probability of multi-hit feature clustering on this slot.")
                else:
                    st.warning("🟡 **ACTION: FINISH CURRENT PHASE OR EXIT**")
                    st.write(f"- **Execution:** Finish only remaining spins in current phase, lock profits, and move to Played Basket.")
                    st.write(f"- **Reason:** Low multi-hit rate ({rehit['multi_hit_rate']}%) in historical logs suggests poor repeat efficiency.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played (Move to Basket)"):
                mark_slot_played(slot_data['slot'])
                st.success(f"Moved '{slot_data['slot']}' to Played Basket!")
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
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0, value=183, step=5)
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
                new_row_df = pd.DataFrame([new_record])

                if not existing_df.empty:
                    for col in existing_cols:
                        if col not in new_row_df.columns:
                            new_row_df[col] = ""
                    updated_df = pd.concat([existing_df.astype(str), new_row_df.astype(str)], ignore_index=True)
                else:
                    updated_df = new_row_df.astype(str)

                conn.update(worksheet="Session Log", data=updated_df)
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
    st.subheader("🤖 Live Strategy AI Agent (Powered by Gemini 3.6)")

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
        prompt_to_submit = "Which slots currently have the highest multi-hit frequency (>30%) and should be re-probed immediately after a feature win?"

    user_input = st.chat_input("Ask your AI Execution Agent anything about today's session strategy...")
    if user_input:
        prompt_to_submit = user_input

    if prompt_to_submit:
        st.session_state.chat_messages.append({"role": "user", "content": prompt_to_submit})
        with st.chat_message("user"):
            st.markdown(prompt_to_submit)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing live matrix & generating strategic plan..."):
                response_text = run_gemini_agent(prompt_to_submit)
                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})

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
            st.rerun()
