import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import math
from datetime import datetime

# Set Streamlit Page Layout
st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

# ==========================================
# 0. STATE MANAGEMENT & CALLBACKS
# ==========================================

TAB_OPTIONS = [
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "📝 Live Data Entry",
    "📈 Visual Data Analytics",
    "🤖 Interactive Agent Chat",
    "🧺 Played Basket & Overrides", 
    "📖 Documentation & Rules"
]

# Single source of truth for Active Tab
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "📊 Today's Priority Board"

# Date & Auto-Day State Management
if "selected_live_date" not in st.session_state:
    st.session_state.selected_live_date = datetime.now().date()

if "selected_live_day" not in st.session_state:
    st.session_state.selected_live_day = datetime.now().strftime("%A")

def update_live_day_callback():
    """Triggers instantly when date picker changes value."""
    new_date = st.session_state.live_date_picker
    st.session_state.selected_live_date = new_date
    st.session_state.selected_live_day = new_date.strftime("%A")

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

# Session State Data Structure Initialization
if "slots_db" not in st.session_state:
    st.session_state.slots_db = build_priority_dataset()

if "played_basket" not in st.session_state:
    st.session_state.played_basket = []
if "display_limit" not in st.session_state:
    st.session_state.display_limit = 30
if "session_start_bankroll" not in st.session_state:
    st.session_state.session_start_bankroll = 1000.0
if "current_bankroll" not in st.session_state:
    st.session_state.current_bankroll = 1000.0
if "session_target" not in st.session_state:
    st.session_state.session_target = 1800.0

if "session_logs" not in st.session_state:
    st.session_state.session_logs = pd.DataFrame([
        {
            "Date": "08/28/2026",
            "Day": "Friday",
            "Family": "Cash Horns",
            "Slot": "Cleopatra’s Kingdom",
            "Spin of Feature Hit": 32,
            "Feature Type": "scatter",
            "Win Amount": 180.0,
            "Win Multiplier": 72.0,
            "Hit Number": 1,
            "Attempt Number": 1
        }
    ])

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = [
        {"role": "assistant", "content": "Welcome! I am your AI Slot Execution Agent. Ask me for real-time recommendations, exit evaluations, rounded check-in amounts, or multi-phase spin execution plans."}
    ]

def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)

# ==========================================
# 3. SIDEBAR CONTROLS & DYNAMIC NAV
# ==========================================

st.sidebar.title("🎰 Live Session Hub")

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
    if st.button("➕ $50 Win"):
        st.session_state.current_bankroll += 50.0
        st.rerun()
with col_sb2:
    if st.button("➖ $50 Loss"):
        st.session_state.current_bankroll -= 50.0
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("Quick Agent Consultations")

if st.sidebar.button("⚡ Suggest 3 Best Available Slots"):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x["base_rvi"], reverse=True)[:3]
    
    agent_msg = "### 🎯 Dynamic Multi-Phase Slot Recommendations:\n\n"
    for idx, item in enumerate(sorted_avail, 1):
        p1 = item['opt_bet']
        p2 = item['step_down_bet']
        alloc = item['checkin_alloc']
        
        agent_msg += f"#### **{idx}. {item['slot']}** ({item['family']})\n"
        agent_msg += f"- **Check-In Allocation:** **${alloc:.2f}** (Rounded cash check-in)\n"
        agent_msg += f"- **Phase 1 (Spins 1–20):** 20 spins @ **${p1:.2f}** bet.\n"
        agent_msg += f"- **Phase 2 (Spins 21–35):** Step down to **${p2:.2f}** bet for 15 spins.\n"
        agent_msg += f"- **Phase 3 (Spike/Exit Rule):** Hard exit after 35 cold spins. On hit >50x, lock core profit & execute **8 Backup Spins** @ **${p2:.2f}**.\n\n"
    
    st.session_state.chat_messages.append({"role": "user", "content": "Suggest the 3 best available slots right now with check-in amounts and proportional step-down bets."})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

if st.sidebar.button("❓ Should I Repeat / Re-Trigger?"):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll
    
    user_q = f"Should I repeat/re-trigger on my current machine? Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}, Current Bankroll: ${st.session_state.current_bankroll:.2f}, Target: ${st.session_state.session_target:.2f}."
    
    if diff > 0 and target_dist <= 200:
        evaluation = f"🎯 **Target Near (${target_dist:.2f} remaining)!**\n\n- **Check-In Cap:** Allocate $50 max.\n- **Phase 1:** Step down bet size to $1.25 / $2.50.\n- **Phase 2:** Execute **5–10 Backup Spins** max.\n- **Exit Rule:** Lock profit immediately once target is hit."
    elif diff > 300:
        evaluation = f"🔥 **Big Win Active (+${diff:.2f})!**\n\n- **Check-In Cap:** Allocate $50 or $100 from profit buffer.\n- **Phase 1:** Lock 80% of win into core balance.\n- **Phase 2:** Execute **8 Backup Spins** at 50% reduced bet.\n- **Exit Rule:** Hard exit if no re-trigger after 8 spins."
    elif diff < -250:
        evaluation = f"⚠️ **Cold Cycle Detected (-${abs(diff):.2f})!**\n\n- **Check-In Cap:** Preserve remaining bankroll.\n- **Phase 1:** Step down bet size to $1.00/$1.25 for 15 spins.\n- **Phase 2:** Hard exit to a fresh slot if no feature hits."
    else:
        evaluation = "✅ **Standard Operational Window.** Continue standard 35-spin multi-phase probe cycle."
        
    st.session_state.chat_messages.append({"role": "user", "content": user_q})
    st.session_state.chat_messages.append({"role": "assistant", "content": f"### 📊 Machine Re-Trigger & Exit Strategy:\n\n{evaluation}"})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# ==========================================
# 4. MAIN DASHBOARD NAVIGATION
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")

# Direct binding to session_state.active_tab fixes lag and double-clicking issues
st.radio(
    "Navigation Tabs",
    options=TAB_OPTIONS,
    key="active_tab",
    horizontal=True,
    label_visibility="collapsed"
)

st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board (Feature-RVI Strategy Matrix)")
    st.caption("Top-ranked slots with spin allocations, rounded check-in capital ($50 increments), and proportional bet structures.")
    
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
            "Total Evaluation Window": f"{item.get('p1_spins', 20) + item.get('p2_spins', 15)} spins max",
            "Suggested Check-In ($)": f"${item['checkin_alloc']:.2f}",
            "Volatility Profile": item["volatility"],
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
    st.caption("Cascading Family $\\rightarrow$ Slot Theme selection for step-by-step game plans and rounded check-in amounts.")
    
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
            c1.metric("Suggested Check-In Amount", f"${checkin:.2f}")
            c2.metric("Phase 1 Bet Size", f"${p1_bet:.2f} (20 Spins)")
            c3.metric("Phase 2 Bet Size", f"${p2_bet:.2f} (15 Spins)")
            c4.metric("Evaluation Window", "35 Spins Total")
            
            st.markdown("---")
            st.markdown("#### 🔄 Proportional Multi-Phase Execution Plan")
            
            st.write(f"**Phase 1: Initial Probe (Spins 1 – 20)**")
            st.write(f"- Insert **${checkin:.2f}** check-in capital (rounded to nearest $50).")
            st.write(f"- Set bet to **${p1_bet:.2f}**.")
            st.write(f"- Play **20 full spins** (Max risk: **${20 * p1_bet:.2f}**).")
            
            st.markdown("---")
            st.write(f"**Phase 2: Proportional Bet Step-Down (Spins 21 – 35 if No Feature)**")
            st.write(f"- If no feature triggers by spin 20, step down bet to **${p2_bet:.2f}** (Proportional ~50% ratio).")
            st.write(f"- Continue probing for **15 additional spins** (Max risk: **${15 * p2_bet:.2f}**).")
            st.write(f"- *Note:* A feature trigger at ${p2_bet:.2f} still yields enough multiplier weight to offset Phase 1 decay.")
            
            st.markdown("---")
            st.write(f"**Phase 3: Spike, Backup Spin & Exit Rule**")
            st.write(f"- **Cold Machine:** If no feature triggers by spin 35, hard exit to next priority slot.")
            st.write(f"- **Big Win Hit:** Lock 80% core profit immediately.")
            st.write(f"- **Backup Spins:** Execute **8 Backup Spins** at **${p2_bet:.2f}** to test for cluster re-triggers.")

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
    st.caption("Matches exact Google Sheet schema. Day of the week updates instantly when changing dates.")

    # Explicit callback on date input updates state immediately on picking from popup calendar
    col_d1, col_d2 = st.columns([1, 2])
    with col_d1:
        st.date_input(
            "Select Date:", 
            value=st.session_state.selected_live_date, 
            key="live_date_picker",
            on_change=update_live_day_callback
        )
    with col_d2:
        st.text_input(
            "Day of Week (Auto-Selected):", 
            value=st.session_state.selected_live_day, 
            disabled=True, 
            key="live_day_display"
        )

    st.markdown("---")

    with st.form("exact_gs_entry_form", clear_on_submit=True):
        col_e1, col_e2, col_e3 = st.columns(3)
        
        with col_e1:
            entry_family = st.selectbox("Slot Family:", list(SLOT_MASTER_LIST.keys()))
            entry_slot = st.selectbox("Slot Theme Name:", SLOT_MASTER_LIST[entry_family])
            entry_spin_hit = st.number_input("Spin of Feature Hit:", min_value=1, max_value=500, value=32)

        with col_e2:
            entry_feat_type = st.selectbox("Feature Type:", ["na", "orb", "scatter", "scatter+orb"])
            entry_win_amt = st.number_input("Win Amount ($):", min_value=0.0, value=150.0, step=10.0)
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0.0, value=50.0, step=5.0)

        with col_e3:
            entry_hit_num = st.number_input("Hit Number:", min_value=1, max_value=20, value=1)
            entry_attempt_num = st.number_input("Attempt Number:", min_value=1, max_value=20, value=1)

        submit_gs_entry = st.form_submit_button("💾 Save Session Record to Dataset")
        
        if submit_gs_entry:
            formatted_date_str = st.session_state.selected_live_date.strftime("%m/%d/%Y")
            current_day_str = st.session_state.selected_live_day

            new_gs_log = {
                "Date": formatted_date_str,
                "Day": current_day_str,
                "Family": entry_family,
                "Slot": entry_slot,
                "Spin of Feature Hit": entry_spin_hit,
                "Feature Type": entry_feat_type,
                "Win Amount": entry_win_amt,
                "Win Multiplier": entry_multiplier,
                "Hit Number": entry_hit_num,
                "Attempt Number": entry_attempt_num
            }
            
            st.session_state.session_logs = pd.concat([st.session_state.session_logs, pd.DataFrame([new_gs_log])], ignore_index=True)
            mark_slot_played(entry_slot)
            
            st.success(f"Logged entry for '{entry_slot}'! Date: {formatted_date_str} ({current_day_str}), Feature Type: '{entry_feat_type}', Win: ${entry_win_amt:.2f}.")

    st.markdown("---")
    st.markdown("#### 📋 Current Logged Entries")
    st.dataframe(st.session_state.session_logs, use_container_width=True)

# ------------------------------------------
# TAB 4: VISUAL DATA ANALYTICS
# ------------------------------------------
elif st.session_state.active_tab == "📈 Visual Data Analytics":
    st.subheader("📈 Visual Data Analytics & Performance Metrics")
    
    df_analytics = st.session_state.session_logs
    if not df_analytics.empty:
        m1, m2, m3 = st.columns(3)
        total_wins = df_analytics["Win Amount"].sum() if "Win Amount" in df_analytics.columns else 0
        avg_mult = df_analytics["Win Multiplier"].mean() if "Win Multiplier" in df_analytics.columns else 0
        total_entries = len(df_analytics)
        
        m1.metric("Total Logged Win Amount", f"${total_wins:,.2f}")
        m2.metric("Average Win Multiplier", f"{avg_mult:.1f}x")
        m3.metric("Total Feature Entries Recorded", total_entries)
        
        st.markdown("---")
        col_g1, col_g2 = st.columns(2)
        
        with col_g1:
            if "Family" in df_analytics.columns and "Win Amount" in df_analytics.columns:
                fig_fam = px.bar(
                    df_analytics, 
                    x="Family", 
                    y="Win Amount", 
                    color="Feature Type" if "Feature Type" in df_analytics.columns else None,
                    title="Total Win Amount by Slot Family ($)",
                    text_auto=True
                )
                st.plotly_chart(fig_fam, use_container_width=True)

        with col_g2:
            if "Spin of Feature Hit" in df_analytics.columns:
                fig_hist = px.histogram(
                    df_analytics, 
                    x="Spin of Feature Hit", 
                    nbins=15, 
                    title="Distribution of Spin Window for Feature Hits"
                )
                st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("No logs available yet for visual analytics.")

# ------------------------------------------
# TAB 5: INTERACTIVE AGENT CHAT
# ------------------------------------------
elif st.session_state.active_tab == "🤖 Interactive Agent Chat":
    st.subheader("🤖 Interactive AI Strategy Partner")
    st.caption("Chat with the strategy agent in real-time for proportional bet plans, rounded check-in amounts, or backup spin rules.")
    
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            
    if prompt := st.chat_input("Ask a strategy question (e.g. 'How much should I check in for $5 base bet?'):"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
            
        reply = f"**Strategy Guidance:** Based on active bankroll **${st.session_state.current_bankroll:.2f}** and target **${st.session_state.session_target:.2f}**:\n\n"
        reply += "1. **Phase 1 Execution:** Play **20 Spins** at your base Phase 1 bet size.\n"
        reply += "2. **Phase 2 Step-Down:** If no feature triggers by spin 20, step down your bet size by ~50% and play **15 Spins**.\n"
        reply += "3. **Check-In Calculation:** Formula = Math.ceil([(20 × Phase 1 Bet) + (15 × Phase 2 Bet)] / 50) * 50 (Rounded up to nearest $50 increment).\n"
        reply += "4. **Backup Spins:** Following a major payout, execute 8 backup spins at the Phase 2 bet size before moving to another machine."
            
        st.session_state.chat_messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)

# ------------------------------------------
# TAB 6: PLAYED BASKET & OVERRIDES
# ------------------------------------------
elif st.session_state.active_tab == "🧺 Played Basket & Overrides":
    st.subheader("Daily Played Basket & Slot Overrides")
    st.caption("Slots played in this session are excluded from recommendations. Click 'Restore' to bring any machine back into your queue.")
    
    if st.session_state.played_basket:
        played_data = []
        for p_slot in st.session_state.played_basket:
            s_match = next((s for s in st.session_state.slots_db if s["slot"] == p_slot), None)
            played_data.append({
                "Slot Name": p_slot,
                "Family": s_match["family"] if s_match else "Unknown",
                "Volatility": s_match["volatility"] if s_match else "N/A"
            })
        
        st.dataframe(pd.DataFrame(played_data), use_container_width=True)
        
        st.markdown("#### 🔓 Restore Slot to Active Recommendations")
        restore_target = st.selectbox("Select Slot to Re-Enable:", options=st.session_state.played_basket)
        if st.button("Unlock Selected Slot"):
            restore_slot(restore_target)
            st.success(f"Restored '{restore_target}' back to priority recommendations!")
            st.rerun()
    else:
        st.info("No slots have been played yet in this active session.")

# ------------------------------------------
# TAB 7: DOCUMENTATION & RULES
# ------------------------------------------
elif st.session_state.active_tab == "📖 Documentation & Rules":
    st.subheader("Strategy Engine Rules & Bounding Logic")
    st.markdown("""
    ### Strategy & Bet Scaling Rules
    1. **Spin Windows:**
       - **Phase 1 Window:** 20 spins @ Base Bet.
       - **Phase 2 Window:** 15 spins @ Step-Down Bet.
       - **Total Probe:** 35 spins max per machine.
    2. **Check-In Capital Calculation:** Suggested Machine Allocation = $(20 \times \text{Phase 1 Bet}) + (15 \times \text{Phase 2 Bet})$, rounded UP to the nearest $\$50$ increment.
    3. **Proportional Bet Tiering ($\le 50\%$ Step-Down Ratio):**
       - $\$10.00 \rightarrow \$5.00$
       - $\$7.50 \rightarrow \$3.75$
       - $\$5.00 \rightarrow \$2.50$
       - $\$3.75 \rightarrow \$2.00$
       - $\$2.50 \rightarrow \$1.25$
    4. **Feature Options:** `na`, `orb`, `scatter`, `scatter+orb`.
    """)
