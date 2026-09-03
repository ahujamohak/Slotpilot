import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import math
import re
from datetime import datetime

# ==========================================
# 0. PAGE CONFIG & STATE MANAGEMENT
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
    """Clears and resets all session data back to default state."""
    st.session_state.played_basket = []
    st.session_state.display_limit = 30
    st.session_state.session_start_bankroll = 1000.0
    st.session_state.current_bankroll = 1000.0
    st.session_state.session_target = 1800.0
    st.session_state.active_tab = "📊 Today's Priority Board"
    st.session_state.show_pivot_form = False
    st.session_state.chat_messages = [
        {"role": "assistant", "content": "Welcome! I am your AI Slot Execution Agent. Ask me for real-time recommendations, exit evaluations, stop-loss checks, or machine pivot commands."}
    ]

if "show_pivot_form" not in st.session_state:
    st.session_state.show_pivot_form = False

if "played_basket" not in st.session_state:
    reset_all_state()

# ==========================================
# 1. MASTER SLOT LIST HIERARCHY
# ==========================================

SLOT_MASTER_LIST = {
    "All Aboard The Lucky Link": ["Go West", "Shinobi"],
    "Balloon Link": ["Australian Outback", "Skull Island"],
    "Bau Zhu Zhao Fu": ["Blue Festival", "Red Festival"],
    "Bull Blitz": ["Golden Empress", "Maximus Money"],
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
# 2. BET SCALING & ALLOCATION MATH
# ==========================================

ALLOWED_BETS = [1.00, 1.25, 2.00, 2.50, 3.00, 3.75, 5.00, 6.25, 7.50, 10.00]

def get_proportional_step_down(p1_bet):
    if p1_bet >= 10.00:
        return 5.00
    elif p1_bet >= 7.50:
        return 3.75
    elif p1_bet >= 6.25:
        return 3.00
    elif p1_bet >= 5.00:
        return 2.50
    elif p1_bet >= 3.75:
        return 2.00
    elif p1_bet >= 2.50:
        return 1.25
    else:
        return 1.00

def round_up_to_nearest_50(val):
    return float(math.ceil(val / 50.0) * 50)

def build_priority_dataset():
    records = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            p1_bet = float(np.random.choice(ALLOWED_BETS))
            p2_bet = get_proportional_step_down(p1_bet)
            raw_alloc = (20 * p1_bet) + (15 * p2_bet)
            checkin_alloc = round_up_to_nearest_50(raw_alloc)

            records.append({
                "family": fam,
                "slot": slot,
                "volatility": np.random.choice(["Med", "Med-High", "High"]),
                "base_rvi": round(float(np.random.uniform(7.5, 9.5)), 2),
                "opt_bet": p1_bet,
                "p1_spins": 20,
                "step_down_bet": p2_bet,
                "p2_spins": 15,
                "checkin_alloc": checkin_alloc
            })
    return records

if "slots_db" not in st.session_state:
    st.session_state.slots_db = build_priority_dataset()

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
# 3. SIDEBAR CONTROLS & NAVIGATION
# ==========================================

st.sidebar.title("🎰 Live Session Hub")

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

    agent_msg = "### 🎯 Dynamic Multi-Phase Recommendations:\n\n"
    for idx, item in enumerate(sorted_avail, 1):
        p1 = item['opt_bet']
        p2 = item['step_down_bet']
        alloc = item['checkin_alloc']

        agent_msg += f"#### **{idx}. {item['slot']}** ({item['family']})\n"
        agent_msg += f"- **Check-In Allocation:** **${alloc:.2f}**\n"
        agent_msg += f"- **Phase 1 (Spins 1–20):** 20 spins @ **${p1:.2f}** bet.\n"
        agent_msg += f"- **Phase 2 (Spins 21–35):** Step down to **${p2:.2f}** bet for 15 spins.\n"
        agent_msg += f"- **Exit Rule:** Exit after 35 cold spins. On hit >50x, execute **8 Backup Spins** @ **${p2:.2f}**.\n\n"

    st.session_state.chat_messages.append({"role": "user", "content": "Suggest the 3 best available slots right now."})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 2. Evaluate Repeat / Re-Trigger
if st.sidebar.button("❓ Should I Repeat / Re-Trigger?", use_container_width=True):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll

    user_q = f"Should I repeat/re-trigger? Starting: ${st.session_state.session_start_bankroll:.2f}, Current: ${st.session_state.current_bankroll:.2f}, Target: ${st.session_state.session_target:.2f}."

    if diff > 0 and target_dist <= 200:
        evaluation = f"🎯 **Target Near (${target_dist:.2f} remaining)!**\n\n- **Check-In Cap:** Allocate $50 max.\n- **Phase 1:** Step down bet size to $1.25 / $2.50.\n- **Phase 2:** Execute **5–10 Backup Spins** max.\n- **Exit Rule:** Lock profit immediately once target is hit."
    elif diff > 300:
        evaluation = f"🔥 **Big Win Active (+${diff:.2f})!**\n\n- **Check-In Cap:** Allocate $50 or $100 from profit buffer.\n- **Phase 1:** Lock 80% of win into core balance.\n- **Phase 2:** Execute **8 Backup Spins** at 50% reduced bet.\n- **Exit Rule:** Hard exit if no re-trigger after 8 spins."
    elif diff < -250:
        evaluation = f"⚠️ **Cold Cycle Detected (-${abs(diff):.2f})!**\n\n- **Check-In Cap:** Preserve remaining bankroll.\n- **Phase 1:** Step down bet size to $1.00/$1.25 for 15 spins.\n- **Phase 2:** Hard exit to a fresh slot if no feature hits."
    else:
        evaluation = "✅ **Standard Operational Window.** Continue standard 35-spin multi-phase probe cycle."

    st.session_state.chat_messages.append({"role": "user", "content": user_q})
    st.session_state.chat_messages.append({"role": "assistant", "content": f"### 📊 Machine Re-Trigger Strategy:\n\n{evaluation}"})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 3. Circuit Breaker / Stop-Loss Evaluation
if st.sidebar.button("🛑 Circuit Breaker / Stop-Loss", use_container_width=True):
    start_b = st.session_state.session_start_bankroll
    curr_b = st.session_state.current_bankroll
    drawdown = start_b - curr_b
    buffer_remaining = curr_b
    top_candidate = get_top_unplayed_slot()

    user_q = f"Evaluate circuit breaker / stop-loss. Starting Bankroll: ${start_b:.2f}, Current: ${curr_b:.2f}."

    rec_plan_str = ""
    if top_candidate:
        rec_slot = top_candidate['slot']
        rec_fam = top_candidate['family']
        rec_rvi = top_candidate['base_rvi']

        p1_min_bet = 1.25
        p2_min_bet = 1.00
        raw_checkin = (20 * p1_min_bet) + (15 * p2_min_bet)
        checkin_min = round_up_to_nearest_50(raw_checkin)

        rec_plan_str = f"""
---
#### 🎰 Dynamic Recovery Plan (Post-Break):
- **Recommended Machine:** **{rec_slot}** ({rec_fam}) | **RVI Score:** {rec_rvi}
- **Check-In Capital:** **${checkin_min:.2f}**
- **Phase 1 (Spins 1–20):** Play **20 spins** @ **${p1_min_bet:.2f}** minimum bet.
- **Phase 2 (Spins 21–35):** Step down to **15 spins** @ **${p2_min_bet:.2f}** minimum bet.
- **Exit Rule:** Hard stop if zero feature hits after 35 spins."""

    if drawdown >= 300:
        agent_out = f"""### 🚨 CIRCUIT BREAKER TRIGGERED (Critical Drawdown)

- **Session Drawdown:** **-${drawdown:.2f}** (Greater than $300 loss threshold)
- **Remaining Session Buffer:** **${buffer_remaining:.2f}**
- **Hard Stop-Loss Limit:** Set hard stop at **${max(0.0, curr_b - 100.0):.2f}**
- **Action Directive:** ⏸️ **MANDATORY 15-MINUTE BREAK**
  - Walk away from the casino floor immediately.
  - Reset mental fatigue and review today's logged performance.
  - Upon return, enforce a **strict minimum bet tier ($1.00 / $1.25)** for all subsequent probes.
{rec_plan_str}"""

    elif drawdown >= 200:
        agent_out = f"""### ⚠️ CIRCUIT BREAKER WARNING (Moderate Cold Streak)

- **Session Drawdown:** **-${drawdown:.2f}** (Hit $200–$300 loss zone)
- **Remaining Session Buffer:** **${buffer_remaining:.2f}**
- **Hard Stop-Loss Limit:** Set hard stop at **${curr_b - 150.0:.2f}**
- **Action Directive:** 📉 **STEP DOWN BET TIER IMMEDIATELY**
  - Lower maximum Phase 1 bet size to **$1.00 / $1.25**.
  - Cap maximum check-in capital at **$50.00** per machine.
  - If drawdown reaches -$300, take an immediate 15-minute break.
{rec_plan_str}"""

    else:
        agent_out = f"""### ✅ CIRCUIT BREAKER STATUS: NORMAL

- **Session Drawdown / Profit:** **{'-$' if drawdown > 0 else '+$'}{abs(drawdown):.2f}**
- **Remaining Buffer:** **${buffer_remaining:.2f}**
- **Action Directive:** Safe to operate within standard bet tiers and 35-spin probe parameters."""

    st.session_state.chat_messages.append({"role": "user", "content": user_q})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_out})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 4. Machine Pivot vs. Stay Advisor
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
# 4. MAIN DASHBOARD CONTENT AREA
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")
st.caption(f"Currently Viewing: **{st.session_state.active_tab}**")
st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board (Feature-RVI Strategy Matrix)")
    st.caption("Top-ranked slots with spin allocations, check-in capital, and proportional bet structures.")

    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_priority = sorted(available_slots, key=lambda x: x["base_rvi"], reverse=True)
    current_display = sorted_priority[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        table_data.append({
            "Rank": rank,
            "Slot Family": item["family"],
            "Slot Theme Name": item["slot"],
            "Phase 1 Bet ($)": f"${item['opt_bet']:.2f}",
            "Phase 1 Spins": f"{item.get('p1_spins', 20)} spins",
            "Phase 2 Bet ($)": f"${item['step_down_bet']:.2f}",
            "Phase 2 Spins": f"{item.get('p2_spins', 15)} spins",
            "Total Window": f"{item.get('p1_spins', 20) + item.get('p2_spins', 15)} spins max",
            "Check-In ($)": f"${item['checkin_alloc']:.2f}",
            "Volatility": item["volatility"],
            "RVI Score": item["base_rvi"]
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
            st.info("All candidates loaded.")

    with col_b:
        st.write(f"Showing **{len(current_display)}** of **{len(sorted_priority)}** unplayed candidates.")

# ------------------------------------------
# TAB 2: PRE-PLANNED EXECUTION CARDS
# ------------------------------------------
elif st.session_state.active_tab == "📋 Pre-Planned Execution Cards":
    st.subheader("Pre-Planned Per-Slot Execution Cards")
    st.caption("Cascading Family selection for step-by-step game plans and check-in amounts.")

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

            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Suggested Check-In", f"${checkin:.2f}")
            c2.metric("Phase 1 Bet", f"${p1_bet:.2f} (20 Spins)")
            c3.metric("Phase 2 Bet", f"${p2_bet:.2f} (15 Spins)")
            c4.metric("Evaluation Window", "35 Spins Total")

            st.markdown("---")
            st.markdown("#### 🔄 Multi-Phase Execution Plan")

            st.write(f"**Phase 1: Initial Probe (Spins 1 – 20)**")
            st.write(f"- Insert **${checkin:.2f}** check-in capital.")
            st.write(f"- Set bet to **${p1_bet:.2f}**.")
            st.write(f"- Play **20 spins** (Max risk: **${20 * p1_bet:.2f}**).")

            st.markdown("---")
            st.write(f"**Phase 2: Bet Step-Down (Spins 21 – 35 if No Feature)**")
            st.write(f"- If no feature triggers by spin 20, step down bet to **${p2_bet:.2f}**.")
            st.write(f"- Continue probing for **15 additional spins** (Max risk: **${15 * p2_bet:.2f}**).")

            st.markdown("---")
            st.write(f"**Phase 3: Spike & Exit Rule**")
            st.write(f"- **Cold Machine:** If no feature triggers by spin 35, hard exit.")
            st.write(f"- **Big Win Hit:** Lock 80% core profit immediately.")
            st.write(f"- **Backup Spins:** Execute **8 Backup Spins** at **${p2_bet:.2f}**.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
                mark_slot_played(slot_data['slot'])
                st.success(f"Moved '{slot_data['slot']}' to Played Basket!")
                st.rerun()

# ------------------------------------------
# TAB 3: LIVE DATA ENTRY (DIRECT GSHEETS WRITE)
# ------------------------------------------
elif st.session_state.active_tab == "📝 Live Data Entry":
    st.subheader("📝 Live Session Data Entry")
    st.caption("Writes directly to your connected Google Sheet. Supports '+' notation (e.g. 35+).")

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

    with st.form("exact_gs_entry_form", clear_on_submit=True):
        col_e1, col_e2, col_e3 = st.columns(3)

        with col_e1:
            entry_spin_hit_raw = st.text_input("Spin of Feature Hit (e.g. 32 or 35+):", value="35")
            entry_feat_type = st.selectbox("Feature Type:", ["na", "orb", "scatter", "scatter+orb"])

        with col_e2:
            entry_win_amt = st.number_input("Win Amount ($):", min_value=0.0, value=0.0, step=10.0)
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0.0, value=0.0, step=5.0)

        with col_e3:
            entry_hit_num = st.number_input("Hit Number:", min_value=1, max_value=20, value=1)
            entry_attempt_num = st.number_input("Attempt Number:", min_value=1, max_value=20, value=1)

        submit_gs_entry = st.form_submit_button("💾 Save Session Record to Google Sheets")

        if submit_gs_entry:
            cleaned_spin_val = entry_spin_hit_raw.strip()

            if entry_feat_type == "na":
                digits = re.sub(r"[^\d]", "", cleaned_spin_val)
                spin_final_str = f"{digits}+" if digits else "35+"
            else:
                spin_final_str = cleaned_spin_val if cleaned_spin_val else "1"

            new_gs_log = pd.DataFrame([{
                "Date": formatted_date_str,
                "Day": dynamic_day,
                "Family": entry_family,
                "Slot": entry_slot,
                "Spin of Feature Hit": spin_final_str,
                "Feature Type": entry_feat_type,
                "Win Amount": entry_win_amt,
                "Win Multiplier": entry_multiplier,
                "Hit Number": entry_hit_num,
                "Attempt Number": entry_attempt_num
            }])

            try:
                # 1. Read existing data from Google Sheets ("Session Log" worksheet)
                existing_data = conn.read(worksheet="Session Log", ttl="0")

                # 2. Append new log
                updated_df = pd.concat([existing_data, new_gs_log], ignore_index=True)

                # 3. Write back to Google Sheets ("Session Log" worksheet)
                conn.update(worksheet="Session Log", data=updated_df)

                mark_slot_played(entry_slot)
                st.success(f"✅ Successfully written to Google Sheets! Entry for '{entry_slot}' saved.")
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# ------------------------------------------
# TAB 4: INTERACTIVE AGENT CHAT
# ------------------------------------------
elif st.session_state.active_tab == "🤖 Interactive Agent Chat":
    st.subheader("🤖 Interactive AI Strategy Partner")
    st.caption("Chat with the strategy agent in real-time or clear chat history.")

    if st.session_state.show_pivot_form:
        with st.expander("🔀 Active Machine Evaluation (Pivot vs. Stay)", expanded=True):
            st.markdown("Enter details for the machine you are currently playing:")

            piv_col1, piv_col2 = st.columns(2)
            with piv_col1:
                curr_fam = st.selectbox("Current Slot Family:", list(SLOT_MASTER_LIST.keys()), key="piv_fam_select")
            with piv_col2:
                curr_slot = st.selectbox("Current Slot Theme:", SLOT_MASTER_LIST[curr_fam], key="piv_slot_select")

            piv_num_col1, piv_num_col2 = st.columns(2)
            with piv_num_col1:
                curr_bet = st.number_input("Current Bet Size ($):", min_value=1.00, value=2.50, step=0.25, key="piv_bet_input")
                spins_done = st.number_input("Spins Completed So Far:", min_value=1, max_value=100, value=15, key="piv_spins_input")

            with piv_num_col2:
                total_return = st.number_input("Total Returns / Wins ($):", min_value=0.0, value=0.0, step=5.0, key="piv_ret_input")
                active_teaser = st.checkbox("Active Orbs/Scatter Teasers Present?", key="piv_teaser_input")

            if st.button("⚡ Evaluate Pivot Decision", type="primary", use_container_width=True):
                st.session_state.show_pivot_form = False

                total_invested = spins_done * curr_bet
                return_pct = (total_return / total_invested * 100) if total_invested > 0 else 0
                next_cand = get_top_unplayed_slot()

                user_msg = f"Evaluating machine performance: Currently on **{curr_slot}** ({curr_fam}). Bet: ${curr_bet:.2f}, Spins Completed: {spins_done}, Returns: ${total_return:.2f} ({return_pct:.1f}% return)."

                next_info_str = ""
                if next_cand:
                    p1_n = next_cand['opt_bet']
                    p2_n = next_cand['step_down_bet']
                    alloc_n = next_cand['checkin_alloc']
                    next_info_str = f"""
#### 🚪 PIVOT EXECUTION PLAN:
- **Next Best Machine:** **{next_cand['slot']}** ({next_cand['family']}) | **RVI:** {next_cand['base_rvi']}
- **Check-In Allocation:** **${alloc_n:.2f}**
- **Phase 1 (Spins 1–20):** 20 spins @ **${p1_n:.2f}** bet.
- **Phase 2 (Spins 21–35):** 15 spins @ **${p2_n:.2f}** bet."""

                if return_pct < 20.0 and not active_teaser:
                    pivot_reply = f"""### 🔀 MACHINE EVALUATION RESULT: COMMAND PIVOT 🚪

- **Machine Status:** Cold Cycle Detected on **{curr_slot}**.
- **Invested Capital:** **${total_invested:.2f}** over {spins_done} spins.
- **Total Returns:** **${total_return:.2f}** ({return_pct:.1f}% return rate).
- **Teaser Status:** No active indicators.

**Directive:** Move immediately. Cut losses on {curr_slot} and pivot to the next priority machine in queue.
{next_info_str}"""

                else:
                    pivot_reply = f"""### 🔀 MACHINE EVALUATION RESULT: COMMAND STAY 🎯

- **Machine Status:** Operational / Warm Window on **{curr_slot}**.
- **Invested Capital:** **${total_invested:.2f}** over {spins_done} spins.
- **Total Returns:** **${total_return:.2f}** ({return_pct:.1f}% return rate).
- **Teaser Status:** {'Teasers Active' if active_teaser else 'Stable return threshold maintained'}.

**Directive:** Stay on machine. Execute a max **8 Backup Spins** at **${get_proportional_step_down(curr_bet):.2f}**. Hard exit if no feature triggers within 8 spins."""

                st.session_state.chat_messages.append({"role": "user", "content": user_msg})
                st.session_state.chat_messages.append({"role": "assistant", "content": pivot_reply})
                st.rerun()

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

    if prompt := st.chat_input("Ask a strategy question:"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        reply = f"**Strategy Guidance:** Based on active bankroll **${st.session_state.current_bankroll:.2f}** and target **${st.session_state.session_target:.2f}**:\n\n"
        reply += "1. **Phase 1 Execution:** Play 20 Spins at Phase 1 bet size.\n"
        reply += "2. **Phase 2 Step-Down:** If no feature triggers by spin 20, step down your bet size by ~50% and play 15 Spins.\n"
        reply += "3. **Stop-Loss Protection:** If session loss exceeds $200–$300, execute immediate Circuit Breaker protocol."

        st.session_state.chat_messages.append({"role": "assistant", "content": reply})
        st.rerun()

# ------------------------------------------
# TAB 5: PLAYED BASKET & OVERRIDES
# ------------------------------------------
elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
    st.subheader("🧺 Played Machine Basket & Manual Overrides")
    st.caption("Manage played slot inventory and restore candidates back into active strategy rotation.")

    if st.session_state.played_basket:
        st.write(f"Currently tracking **{len(st.session_state.played_basket)}** played machine(s):")

        for slot_item in list(st.session_state.played_basket):
            col_p1, col_p2 = st.columns([3, 1])
            with col_p1:
                st.write(f"• **{slot_item}**")
            with col_p2:
                if st.button(f"🔄 Restore", key=f"restore_{slot_item}"):
                    restore_slot(slot_item)
                    st.success(f"Restored '{slot_item}' to Priority Queue!")
                    st.rerun()
    else:
        st.info("No machines currently in the played basket.")
