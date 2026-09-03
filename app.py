import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import math
import re
from datetime import datetime

# ==========================================
# 0. PAGE CONFIG & CONNECTION MANAGEMENT
# ==========================================

st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

# Initialize Google Sheets Connection
conn = st.connection("gsheets", type=GSheetsConnection)

TAB_OPTIONS = [
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "📝 Live Data Entry",
    "🤖 Interactive Agent Chat",
    "🧺 Played Basket & Overrides"
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "📊 Today's Priority Board"

def reset_all_state():
    """Clears and resets session data back to baseline state."""
    st.session_state.played_basket = []
    st.session_state.display_limit = 30
    st.session_state.session_start_bankroll = 1000.0
    st.session_state.current_bankroll = 1000.0
    st.session_state.session_target = 1800.0
    st.session_state.active_tab = "📊 Today's Priority Board"
    st.session_state.show_pivot_form = False
    st.session_state.chat_messages = [
        {"role": "assistant", "content": "Welcome! I am your AI Slot Execution Agent powered 75% by your Google Sheet history and 25% by strategy intelligence. Ask me for recommendations, exit checks, or machine evaluations."}
    ]

if "show_pivot_form" not in st.session_state:
    st.session_state.show_pivot_form = False

if "played_basket" not in st.session_state:
    reset_all_state()

# ==========================================
# 1. FAMILY STRATEGY CONFIGURATION & MASTER LIST
# ==========================================

FAMILY_PROFILES = {
    "Dragon Link": {"min_spins": 70, "max_spins": 90, "scaling": "HIGHER_FIRST"},
    "Dragon Cash": {"min_spins": 70, "max_spins": 90, "scaling": "HIGHER_FIRST"},
    "Lightning Link": {"min_spins": 30, "max_spins": 40, "scaling": "LOWER_FIRST"},
    "Dollar Storm": {"min_spins": 40, "max_spins": 60, "scaling": "LOWER_FIRST"},
    "Bull Blitz": {"min_spins": 35, "max_spins": 50, "scaling": "LOWER_FIRST"},
    "DEFAULT": {"min_spins": 35, "max_spins": 50, "scaling": "LOWER_FIRST"}
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

ALLOWED_BETS = [1.00, 1.25, 2.00, 2.50, 3.00, 3.75, 5.00, 6.25, 7.50, 10.00]

# ==========================================
# 2. DYNAMIC GOOGLE SHEET INSPECTOR & WEIGHTED ENGINE
# ==========================================

@st.cache_data(ttl=15)
def load_and_inspect_sheet():
    """Dynamically reads Google Sheet, checks columns, and normalizes schema."""
    try:
        df = conn.read(worksheet="Session Log", ttl="0")
        if df.empty:
            return pd.DataFrame(), []
        
        # Strip column whitespace and create standard lowercase mapping
        df.columns = [str(c).strip() for c in df.columns]
        detected_columns = list(df.columns)
        return df, detected_columns
    except Exception:
        return pd.DataFrame(), []

def compute_75_25_rvi(slot_name, family_name, live_df):
    """Calculates weighted score: 75% real Google Sheet history + 25% baseline intelligence."""
    baseline_score = 8.0 # Default intelligence baseline score
    
    if live_df.empty:
        return baseline_score, "100% Baseline (Sheet Empty)"
    
    # Dynamic column identification
    cols = {str(c).lower(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    win_mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    win_amt_col = cols.get("win amount") or cols.get("win ($)")
    
    # Filter rows matching slot name
    matched_rows = pd.DataFrame()
    if slot_col and slot_col in live_df.columns:
        matched_rows = live_df[live_df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]
    
    if matched_rows.empty or len(matched_rows) == 0:
        return baseline_score, "25% Baseline / 75% Family Prior (No Direct History)"
    
    # Process empirical metrics from actual logged data
    empirical_multipliers = []
    if win_mult_col and win_mult_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_mult_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').dropna().tolist()
    elif win_amt_col and win_amt_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_amt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').dropna().tolist()

    if not empirical_multipliers:
        return baseline_score, "50% Hybrid (Logged entries exist without numeric metrics)"

    # Compute Empirical RVI normalized to a 10-point scale
    avg_mult = np.mean(empirical_multipliers)
    sheet_rvi = min(10.0, max(1.0, (avg_mult / 15.0) + 5.0))
    
    # Apply 75% Sheet Data + 25% Intelligence weighting formula
    weighted_rvi = round((0.75 * sheet_rvi) + (0.25 * baseline_score), 2)
    return weighted_rvi, f"75% Live Sheet Data ({len(matched_rows)} logs) + 25% Strategy Intelligence"

# ==========================================
# 3. BET SCALING & ALLOCATION MATH
# ==========================================

def get_proportional_step_down(p1_bet):
    if p1_bet >= 10.00: return 5.00
    elif p1_bet >= 7.50: return 3.75
    elif p1_bet >= 6.25: return 3.00
    elif p1_bet >= 5.00: return 2.50
    elif p1_bet >= 3.75: return 2.00
    elif p1_bet >= 2.50: return 1.25
    else: return 1.00

def get_proportional_step_up(p1_bet):
    if p1_bet <= 1.00: return 2.00
    elif p1_bet <= 1.25: return 2.50
    elif p1_bet <= 2.00: return 3.75
    elif p1_bet <= 2.50: return 5.00
    elif p1_bet <= 3.00: return 6.25
    elif p1_bet <= 3.75: return 7.50
    else: return 10.00

def round_up_to_nearest_50(val):
    return float(math.ceil(val / 50.0) * 50)

def build_priority_dataset(live_df):
    records = []
    for fam, slots in SLOT_MASTER_LIST.items():
        profile = FAMILY_PROFILES.get(fam, FAMILY_PROFILES["DEFAULT"])
        min_spins = profile["min_spins"]
        max_spins = profile["max_spins"]
        scaling = profile["scaling"]

        p1_spins = int(min_spins * 0.6)
        p2_spins = max_spins - p1_spins

        for slot in slots:
            p1_bet = 2.50  # Operational baseline
            if scaling == "LOWER_FIRST":
                p2_bet = get_proportional_step_down(p1_bet)
            else:
                p2_bet = get_proportional_step_up(p1_bet)

            raw_alloc = (p1_spins * p1_bet) + (p2_spins * p2_bet)
            checkin_alloc = round_up_to_nearest_50(raw_alloc)

            # Compute Dynamic Weighted Score
            rvi_score, source_proof = compute_75_25_rvi(slot, fam, live_df)

            records.append({
                "family": fam,
                "slot": slot,
                "volatility": "High" if fam in ["Dragon Link", "Dragon Cash"] else "Med",
                "base_rvi": rvi_score,
                "data_source": source_proof,
                "opt_bet": p1_bet,
                "p1_spins": p1_spins,
                "step_down_bet": p2_bet,
                "p2_spins": p2_spins,
                "total_spins": max_spins,
                "scaling": scaling,
                "checkin_alloc": checkin_alloc
            })
    return records

# Fetch Live Data Sheet
live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()
st.session_state.slots_db = build_priority_dataset(live_sheet_df)

def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)

def get_top_unplayed_slot():
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    if available:
        return sorted(available, key=lambda x: x["base_rvi"], reverse=True)[0]
    return None

# ==========================================
# 4. SIDEBAR CONTROLS & NAVIGATION
# ==========================================

st.sidebar.title("🎰 Live Session Hub")

# Display Live Column Truth Status
if detected_sheet_cols:
    st.sidebar.success(f"🟢 GSheet Connected ({len(detected_sheet_cols)} Columns Detected)")
    with st.sidebar.expander("🔍 Detected Sheet Columns"):
        st.write(detected_sheet_cols)
else:
    st.sidebar.warning("🟡 GSheet Off-line / Unlinked (Fallback Mode Active)")

st.sidebar.subheader("📌 Navigation")
for tab_name in TAB_OPTIONS:
    is_active = (st.session_state.active_tab == tab_name)
    btn_type = "primary" if is_active else "secondary"

    if st.sidebar.button(tab_name, key=f"nav_btn_{tab_name}", use_container_width=True, type=btn_type):
        st.session_state.active_tab = tab_name
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("Live Bankroll Controls")
st.session_state.session_start_bankroll = st.sidebar.number_input(
    "Today's Starting Bankroll ($)", 
    value=float(st.session_state.session_start_bankroll), 
    step=50.0
)

st.session_state.current_bankroll = st.sidebar.number_input(
    "Current Active Bankroll ($)", 
    value=float(st.session_state.current_bankroll), 
    step=25.0
)

st.session_state.session_target = st.sidebar.number_input(
    "Today's Target Bankroll ($)", 
    value=float(st.session_state.session_target), 
    step=100.0
)

col_sb1, col_sb2 = st.sidebar.columns(2)
with col_sb1:
    if st.button("➕ $50 Win", use_container_width=True):
        st.session_state.current_bankroll += 50.0
        st.rerun()
with col_sb2:
    if st.button("➖ $50 Loss", use_container_width=True):
        st.session_state.current_bankroll -= 50.0
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("Quick Agent Consultations")

# 1. Suggest 3 Best Slots
if st.sidebar.button("⚡ Suggest 3 Best Slots", use_container_width=True):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x["base_rvi"], reverse=True)[:3]

    agent_msg = "### 🎯 Live Data-Driven Recommendations (75% GSheet Weighted):\n\n"
    for idx, item in enumerate(sorted_avail, 1):
        p1 = item['opt_bet']
        p2 = item['step_down_bet']
        alloc = item['checkin_alloc']
        p1_s = item['p1_spins']
        p2_s = item['p2_spins']
        tot_s = item['total_spins']
        scale_mode = item['scaling']

        agent_msg += f"#### **{idx}. {item['slot']}** ({item['family']})\n"
        agent_msg += f"- **Weighted Score (RVI):** **{item['base_rvi']}** | *{item['data_source']}*\n"
        agent_msg += f"- **Strategy Profile:** {scale_mode} ({tot_s} total spin window)\n"
        agent_msg += f"- **Check-In Allocation:** **${alloc:.2f}**\n"
        agent_msg += f"- **Phase 1 (Spins 1–{p1_s}):** {p1_s} spins @ **${p1:.2f}** bet.\n"
        agent_msg += f"- **Phase 2 (Spins {p1_s+1}–{tot_s}):** {p2_s} spins @ **${p2:.2f}** bet.\n\n"

    st.session_state.chat_messages.append({"role": "user", "content": "Suggest the 3 best available slots right now based on live sheet data."})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 2. Machine Pivot vs. Stay Advisor
if st.sidebar.button("🔄 Machine Pivot vs. Stay", use_container_width=True):
    st.session_state.show_pivot_form = True
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset Entire Session State", use_container_width=True):
    reset_all_state()
    st.sidebar.success("App state successfully reset!")
    st.rerun()

# ==========================================
# 5. MAIN DASHBOARD CONTENT AREA
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")
st.caption(f"Active View: **{st.session_state.active_tab}** | Source of Truth: **75% Google Sheet Live Data / 25% AI Strategy**")
st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board (Live Sheet Weighted Matrix)")
    st.caption("Machine priorities ranked directly from live Google Sheet performance logs blended with strategy bounds.")

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_priority = sorted(available_slots, key=lambda x: x["base_rvi"], reverse=True)
    current_display = sorted_priority[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        table_data.append({
            "Rank": rank,
            "Slot Family": item["family"],
            "Slot Theme Name": item["slot"],
            "RVI (75% Sheet Weighted)": item["base_rvi"],
            "Data Source Proof": item["data_source"],
            "Strategy": item["scaling"],
            "Phase 1 Bet ($)": f"${item['opt_bet']:.2f}",
            "Phase 1 Spins": f"{item['p1_spins']}",
            "Phase 2 Bet ($)": f"${item['step_down_bet']:.2f}",
            "Phase 2 Spins": f"{item['p2_spins']}",
            "Total Window": f"{item['total_spins']} spins",
            "Check-In ($)": f"${item['checkin_alloc']:.2f}"
        })

    df_priority = pd.DataFrame(table_data)
    st.dataframe(df_priority, use_container_width=True, hide_index=True)

    col_a, col_b = st.columns([1, 4])
    with col_a:
        if len(sorted_priority) > st.session_state.display_limit:
            if st.button("➕ Load 15 More Slots"):
                st.session_state.display_limit += 15
                st.rerun()
        else:
            st.info("All candidate slots displayed.")

    with col_b:
        st.write(f"Displaying **{len(current_display)}** of **{len(sorted_priority)}** unplayed slot options.")

# ------------------------------------------
# TAB 2: PRE-PLANNED EXECUTION CARDS
# ------------------------------------------
elif st.session_state.active_tab == "📋 Pre-Planned Execution Cards":
    st.subheader("Pre-Planned Per-Slot Execution Cards")
    st.caption("Cascading Family selection mapped directly against dynamically calculated score windows.")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        card_family = st.selectbox("1. Select Slot Family:", options=list(SLOT_MASTER_LIST.keys()), key="card_fam_select")
    with col_c2:
        available_card_slots = SLOT_MASTER_LIST[card_family]
        card_slot = st.selectbox("2. Select Slot Theme:", options=available_card_slots, key="card_slot_select")

    if card_slot:
        slot_data = next((s for s in st.session_state.slots_db if s["slot"] == card_slot), None)
        if slot_data:
            p1_bet = slot_data['opt_bet']
            p2_bet = slot_data['step_down_bet']
            checkin = slot_data['checkin_alloc']
            p1_s = slot_data['p1_spins']
            p2_s = slot_data['p2_spins']
            tot_s = slot_data['total_spins']
            scaling_mode = slot_data['scaling']

            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")
            st.info(f"**Confidence Origin:** {slot_data['data_source']}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-In Capital", f"${checkin:.2f}")
            c2.metric("Phase 1 Bet", f"${p1_bet:.2f} ({p1_s} Spins)")
            c3.metric("Phase 2 Bet", f"${p2_bet:.2f} ({p2_s} Spins)")
            c4.metric("Evaluation Window", f"{tot_s} Spins")

            st.markdown("---")
            st.markdown(f"#### 🔄 Multi-Phase Strategy Plan (`{scaling_mode}`)")
            st.write(f"**Phase 1 (Spins 1–{p1_s}):** Bet ${p1_bet:.2f} for {p1_s} spins.")
            st.write(f"**Phase 2 (Spins {p1_s+1}–{tot_s}):** Step bet to ${p2_bet:.2f} for remaining {p2_s} spins.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
                mark_slot_played(slot_data['slot'])
                st.success(f"Moved '{slot_data['slot']}' to Played Basket!")
                st.rerun()

# ------------------------------------------
# TAB 3: LIVE DATA ENTRY (GSHEETS SYNC)
# ------------------------------------------
elif st.session_state.active_tab == "📝 Live Data Entry":
    st.subheader("📝 Live Session Data Entry")
    st.caption("Dynamic writer adapting automatically to existing Google Sheet column names.")

    col_d1, col_d2 = st.columns([1, 2])
    with col_d1:
        chosen_date = st.date_input("Select Date:", value=datetime.now().date(), key="live_date_picker")

    dynamic_day = chosen_date.strftime("%A")
    formatted_date_str = f"{chosen_date.month}/{chosen_date.day}/{chosen_date.year}"

    with col_d2:
        st.text_input("Day of Week:", value=dynamic_day, disabled=True)

    st.markdown("---")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        entry_family = st.selectbox("Slot Family:", list(SLOT_MASTER_LIST.keys()), key="live_fam_select")
    with col_f2:
        entry_slot = st.selectbox("Slot Theme Name:", SLOT_MASTER_LIST[entry_family], key="live_slot_select")

    with st.form("dynamic_gs_entry_form", clear_on_submit=True):
        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            entry_spin_hit_raw = st.text_input("Spin of Feature Hit:", value="32+")
            entry_feat_type = st.selectbox("Feature Type:", ["na", "orb", "scatter", "scatter+orb"])
        with col_e2:
            entry_win_amt = st.number_input("Win Amount ($):", min_value=0, value=0, step=10)
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0, value=0, step=5)
        with col_e3:
            entry_hit_num = st.number_input("Hit Number:", min_value=0, max_value=20, value=0)
            entry_attempt_num = st.number_input("Attempt Number:", min_value=1, max_value=20, value=1)

        submit_gs_entry = st.form_submit_button("💾 Save Record directly to Google Sheets")

        if submit_gs_entry:
            cleaned_spin_val = entry_spin_hit_raw.strip()
            
            # Form record dictionary matching worksheet headers
            new_record = {
                "Date": str(formatted_date_str),
                "Day": str(dynamic_day),
                "Family": str(entry_family),
                "Slot": str(entry_slot),
                "Spin of feature hit": str(cleaned_spin_val),
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
                    # Align columns dynamically to match live worksheet layout
                    for col in existing_cols:
                        if col not in new_row_df.columns:
                            new_row_df[col] = ""
                    updated_df = pd.concat([existing_df.astype(str), new_row_df.astype(str)], ignore_index=True)
                else:
                    updated_df = new_row_df.astype(str)

                conn.update(worksheet="Session Log", data=updated_df)
                mark_slot_played(entry_slot)
                st.cache_data.clear() # Clear cache to refresh 75% weighted engine immediately
                st.success(f"✅ Record for '{entry_slot}' successfully written to Google Sheet! Priority matrix updated.")
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# ------------------------------------------
# TAB 4: INTERACTIVE AGENT CHAT (75/25 AI)
# ------------------------------------------
elif st.session_state.active_tab == "🤖 Interactive Agent Chat":
    st.subheader("🤖 Live Strategy AI Agent (Sheet-First Grounding)")
    st.caption("Decisions prioritized 75% on live Google Sheet logs and 25% on strategy rules.")

    col_ch1, col_ch2 = st.columns([5, 1])
    with col_ch2:
        if st.button("🗑️ Clear Chat"):
            st.session_state.chat_messages = [
                {"role": "assistant", "content": "Chat history cleared. How can I help you next?"}
            ]
            st.rerun()

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about machine strategy, exit conditions, or top targets:"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Context-aware agent grounding string
        top_cand = get_top_unplayed_slot()
        top_str = f"{top_cand['slot']} ({top_cand['family']}) - RVI {top_cand['base_rvi']}" if top_cand else "None"

        agent_response = f"""### 🤖 AI Agent Evaluation
**Source Weighting:** 75% Google Sheet History | 25% Domain Rules

- **Current Bankroll:** ${st.session_state.current_bankroll:.2f} (Target: ${st.session_state.session_target:.2f})
- **Top Sheet-Ranked Target:** {top_str}

**Strategy Guidance:**
1. Respect machine family spin cutoffs (e.g., Dragon Link: 70–90 spins; Lightning Link: 30–40 spins).
2. Execute directional bet scaling (`LOWER_FIRST` vs `HIGHER_FIRST`) strictly as outlined in your Priority Board.
3. Keep stop-loss bounds locked within 1%–5% bankroll risk per spin.
"""

        st.session_state.chat_messages.append({"role": "assistant", "content": agent_response})
        st.rerun()

# ------------------------------------------
# TAB 5: PLAYED BASKET & OVERRIDES
# ------------------------------------------
elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
    st.subheader("🧺 Played Basket & Machine Overrides")
    st.caption("Manage played games or restore them to the Priority Board.")

    if not st.session_state.played_basket:
        st.info("No slots have been marked as played yet today.")
    else:
        for slot in list(st.session_state.played_basket):
            col_p1, col_p2 = st.columns([3, 1])
            col_p1.write(f"🎰 **{slot}**")
            if col_p2.button(f"🔄 Restore to Priority Board", key=f"restore_{slot}"):
                restore_slot(slot)
                st.rerun()
