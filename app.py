import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import math
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
        {"role": "assistant", "content": "Welcome! I am your AI Slot Execution Agent powered 75% by your Google Sheet history and 25% by strategy intelligence."}
    ]

if "show_pivot_form" not in st.session_state:
    st.session_state.show_pivot_form = False

if "played_basket" not in st.session_state:
    reset_all_state()

# ==========================================
# 1. FAMILY STRATEGY CONFIGURATION & MASTER LIST
# ==========================================

FAMILY_PROFILES = {
    "Dragon Link": {"base_min_spins": 70, "base_max_spins": 110, "scaling": "HIGHER_FIRST"},
    "Dragon Cash": {"base_min_spins": 70, "base_max_spins": 110, "scaling": "HIGHER_FIRST"},
    "Lightning Link": {"base_min_spins": 30, "base_max_spins": 50, "scaling": "LOWER_FIRST"},
    "Dollar Storm": {"base_min_spins": 40, "base_max_spins": 70, "scaling": "LOWER_FIRST"},
    "Bull Blitz": {"base_min_spins": 35, "base_max_spins": 65, "scaling": "LOWER_FIRST"},
    "DEFAULT": {"base_min_spins": 35, "base_max_spins": 60, "scaling": "LOWER_FIRST"}
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
# 2. DYNAMIC GOOGLE SHEET INSPECTOR & WEIGHTED ENGINE
# ==========================================

@st.cache_data(ttl=15)
def load_and_inspect_sheet():
    """Dynamically reads Google Sheet, checks columns, and normalizes schema."""
    try:
        df = conn.read(worksheet="Session Log", ttl="0")
        if df.empty:
            return pd.DataFrame(), []
        df.columns = [str(c).strip() for c in df.columns]
        return df, list(df.columns)
    except Exception:
        return pd.DataFrame(), []

def compute_75_25_rvi(slot_name, family_name, live_df):
    """Calculates weighted score: 75% real Google Sheet history + 25% baseline intelligence."""
    baseline_score = 7.5
    
    if live_df.empty:
        return baseline_score, "100% Baseline"
    
    cols = {str(c).lower(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    win_mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    win_amt_col = cols.get("win amount") or cols.get("win ($)")
    
    matched_rows = pd.DataFrame()
    if slot_col and slot_col in live_df.columns:
        matched_rows = live_df[live_df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()]
    
    if matched_rows.empty or len(matched_rows) == 0:
        return baseline_score, "25% Baseline / 75% Family Prior"
    
    empirical_multipliers = []
    if win_mult_col and win_mult_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_mult_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').dropna().tolist()
    elif win_amt_col and win_amt_col in matched_rows.columns:
        empirical_multipliers = pd.to_numeric(matched_rows[win_amt_col].astype(str).str.extract(r'(\d+)')[0], errors='coerce').dropna().tolist()

    if not empirical_multipliers:
        return baseline_score, "50% Hybrid"

    avg_mult = np.mean(empirical_multipliers)
    sheet_rvi = min(10.0, max(1.0, (avg_mult / 15.0) + 5.0))
    weighted_rvi = round((0.75 * sheet_rvi) + (0.25 * baseline_score), 2)
    return weighted_rvi, f"75% Live Sheet Data ({len(matched_rows)} logs)"

# ==========================================
# 3. DYNAMIC BET & SPIN ALLOCATION MATH
# ==========================================

def calculate_dynamic_bet_and_spins(rvi_score, family_name):
    """
    Dynamically scales bets and spin limits based on RVI score:
    - High RVI (best performers): High Bet Levels ($5.00, $7.50, $10.00) & Expanded Spin Windows
    - Average/Unproven RVI: Standard Baseline Bets ($1.25, $2.50) & Standard Spin Windows
    """
    profile = FAMILY_PROFILES.get(family_name, FAMILY_PROFILES["DEFAULT"])
    base_min = profile["base_min_spins"]
    base_max = profile["base_max_spins"]
    scaling = profile["scaling"]

    # Scale total spins dynamically based on performance score
    if rvi_score >= 8.5:
        total_spins = int(base_max * 1.3)  # Extended evaluation for top performers
        p1_bet = 7.50 if rvi_score >= 9.0 else 5.00
        p2_bet = 10.00 if scaling == "HIGHER_FIRST" else 3.75
    elif rvi_score >= 7.5:
        total_spins = base_max
        p1_bet = 3.75
        p2_bet = 5.00 if scaling == "HIGHER_FIRST" else 2.50
    else:
        total_spins = base_min
        p1_bet = 2.50
        p2_bet = 3.75 if scaling == "HIGHER_FIRST" else 1.25

    p1_spins = int(total_spins * 0.6)
    p2_spins = total_spins - p1_spins

    raw_alloc = (p1_spins * p1_bet) + (p2_spins * p2_bet)
    checkin_alloc = float(math.ceil(raw_alloc / 50.0) * 50)

    return p1_bet, p1_spins, p2_bet, p2_spins, total_spins, checkin_alloc

def build_priority_dataset(live_df):
    records = []
    
    # Calculate RVI scores first for all slots
    slot_scores = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rvi_score, source_proof = compute_75_25_rvi(slot, fam, live_df)
            slot_scores.append({
                "family": fam,
                "slot": slot,
                "rvi": rvi_score,
                "source_proof": source_proof
            })

    # Sort slots by RVI score descending so top performers get assigned top bets
    slot_scores = sorted(slot_scores, key=lambda x: x["rvi"], reverse=True)

    for idx, item in enumerate(slot_scores):
        fam = item["family"]
        slot = item["slot"]
        rvi_score = item["rvi"]

        # Boost top 10% overall performers to top-tier high bets
        if idx < max(1, int(len(slot_scores) * 0.10)) and rvi_score >= 7.5:
            rvi_score = max(rvi_score, 8.8)

        p1_bet, p1_spins, p2_bet, p2_spins, total_spins, checkin_alloc = calculate_dynamic_bet_and_spins(rvi_score, fam)

        records.append({
            "family": fam,
            "slot": slot,
            "base_rvi": rvi_score,
            "data_source": item["source_proof"],
            "opt_bet": p1_bet,
            "p1_spins": p1_spins,
            "step_down_bet": p2_bet,
            "p2_spins": p2_spins,
            "total_spins": total_spins,
            "scaling": FAMILY_PROFILES.get(fam, FAMILY_PROFILES["DEFAULT"])["scaling"],
            "checkin_alloc": checkin_alloc
        })
    return sorted(records, key=lambda x: x["base_rvi"], reverse=True)

# Fetch Live Data Sheet
live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()
st.session_state.slots_db = build_priority_dataset(live_sheet_df)

def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)

# ==========================================
# 4. SIDEBAR CONTROLS & NAVIGATION
# ==========================================

st.sidebar.title("🎰 Live Session Hub")

if detected_sheet_cols:
    st.sidebar.success(f"🟢 GSheet Connected ({len(detected_sheet_cols)} Columns)")
else:
    st.sidebar.warning("🟡 GSheet Off-line (Fallback Mode)")

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

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset Entire Session State", use_container_width=True):
    reset_all_state()
    st.sidebar.success("App state reset!")
    st.rerun()

# ==========================================
# 5. MAIN DASHBOARD CONTENT AREA
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")
st.caption(f"Active View: **{st.session_state.active_tab}**")
st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board (Live Sheet Weighted Matrix)")
    st.caption("Highest RVI performers dynamically receive higher bet levels ($5.00–$10.00) and expanded spin windows.")

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    current_display = available_slots[:st.session_state.display_limit]

    # Clean priority table without "Data Source Proof" or "Strategy" columns
    table_data = []
    for rank, item in enumerate(current_display, 1):
        table_data.append({
            "Rank": rank,
            "Slot Family": item["family"],
            "Slot Theme Name": item["slot"],
            "RVI Score": item["base_rvi"],
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
        if len(available_slots) > st.session_state.display_limit:
            if st.button("➕ Load 15 More Slots"):
                st.session_state.display_limit += 15
                st.rerun()
        else:
            st.info("All slots displayed.")

    with col_b:
        st.write(f"Displaying **{len(current_display)}** of **{len(available_slots)}** unplayed slots.")

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
        slot_data = next((s for s in st.session_state.slots_db if s["slot"] == card_slot), None)
        if slot_data:
            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-In Capital", f"${slot_data['checkin_alloc']:.2f}")
            c2.metric("Phase 1 Bet", f"${slot_data['opt_bet']:.2f} ({slot_data['p1_spins']} Spins)")
            c3.metric("Phase 2 Bet", f"${slot_data['step_down_bet']:.2f} ({slot_data['p2_spins']} Spins)")
            c4.metric("Total Evaluation Window", f"{slot_data['total_spins']} Spins")

            st.markdown("---")
            st.markdown("#### 🔄 Step Execution Plan")
            st.write(f"**Phase 1:** Bet **${slot_data['opt_bet']:.2f}** for **{slot_data['p1_spins']}** spins.")
            st.write(f"**Phase 2:** Adjust bet to **${slot_data['step_down_bet']:.2f}** for remaining **{slot_data['p2_spins']}** spins.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
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
                st.success(f"✅ Recorded '{entry_slot}'! Priority matrix updated.")
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# ------------------------------------------
# TAB 4: INTERACTIVE AGENT CHAT
# ------------------------------------------
elif st.session_state.active_tab == "🤖 Interactive Agent Chat":
    st.subheader("🤖 Live Strategy AI Agent")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about machine strategy, exit conditions, or top targets:"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
        top_cand = available[0] if available else None
        top_str = f"{top_cand['slot']} ({top_cand['family']}) - Bet: ${top_cand['opt_bet']:.2f}, RVI: {top_cand['base_rvi']}" if top_cand else "None"

        agent_response = f"""### 🤖 AI Agent Evaluation
- **Active Bankroll:** ${st.session_state.current_bankroll:.2f} (Target: ${st.session_state.session_target:.2f})
- **Top Sheet-Ranked Target:** {top_str}

**Strategy Guidance:**
1. Top performers currently scale up to **${top_cand['opt_bet']:.2f}** per spin.
2. Maintain strict spin limits; exit if no feature hits within the allocated total spin window.
"""
        st.session_state.chat_messages.append({"role": "assistant", "content": agent_response})
        st.rerun()

# ------------------------------------------
# TAB 5: PLAYED BASKET & OVERRIDES
# ------------------------------------------
elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
    st.subheader("🧺 Played Basket & Machine Overrides")

    if not st.session_state.played_basket:
        st.info("No slots marked as played today.")
    else:
        for slot in list(st.session_state.played_basket):
            col_p1, col_p2 = st.columns([3, 1])
            col_p1.write(f"🎰 **{slot}**")
            if col_p2.button("🔄 Restore to Priority Board", key=f"restore_{slot}"):
                restore_slot(slot)
                st.rerun()
