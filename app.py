import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime

# Set Streamlit Page Layout
st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

# Initialize Active Tab Tracker
TAB_OPTIONS = [
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "📝 Live Data Entry",
    "📈 Visual Data Analytics",
    "🤖 Interactive Agent Chat",
    "🧺 Played Basket & Overrides", 
    "📖 Documentation & Rules"
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "📊 Today's Priority Board"

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
# 2. STRICT REALISTIC DENOM & BET MATH
# ==========================================

def get_realistic_bet_and_denom():
    """
    Strict Casino Betting Rules:
    - 5c Denom (25 lines max): Minimum bet $1.25. Allowed: $1.25, $2.50, $3.75, $5.00, $6.25, $7.50, $10.00.
    - 10c Denom: Minimum bet $2.50. Allowed: $2.50, $5.00, $7.50, $10.00.
    - 2c Denom (50 lines): $1.00 (1x), $2.00 (2x), $3.00 (3x), $5.00 (5x), $10.00 (10x). [NO $2.50 on 2c]
    - 1c Denom (50 lines): $0.50 (1x), $1.00 (2x), $1.50 (3x), $2.50 (5x), $5.00 (10x).
    - $1.00 Denom: Prioritizes $10.00 bets for maximum base RTP.
    """
    valid_configs = [
        {"denom": "1c", "bet": 1.00, "lines": 50, "mult": 2},
        {"denom": "2c", "bet": 1.00, "lines": 50, "mult": 1},
        {"denom": "5c", "bet": 1.25, "lines": 25, "mult": 1},
        {"denom": "2c", "bet": 2.00, "lines": 50, "mult": 2},
        {"denom": "5c", "bet": 2.50, "lines": 25, "mult": 2},
        {"denom": "10c", "bet": 2.50, "lines": 25, "mult": 1},
        {"denom": "2c", "bet": 3.00, "lines": 50, "mult": 3},
        {"denom": "5c", "bet": 3.75, "lines": 25, "mult": 3},
        {"denom": "5c", "bet": 5.00, "lines": 25, "mult": 4},
        {"denom": "10c", "bet": 5.00, "lines": 25, "mult": 2},
        {"denom": "2c", "bet": 5.00, "lines": 50, "mult": 5},
        {"denom": "5c", "bet": 6.25, "lines": 25, "mult": 5},
        {"denom": "5c", "bet": 7.50, "lines": 25, "mult": 6},
        {"denom": "10c", "bet": 7.50, "lines": 25, "mult": 3},
        {"denom": "$1", "bet": 10.00, "lines": 10, "mult": 1},
        {"denom": "10c", "bet": 10.00, "lines": 25, "mult": 4},
        {"denom": "5c", "bet": 10.00, "lines": 25, "mult": 8}
    ]
    return valid_configs[np.random.choice(len(valid_configs))]

def build_priority_dataset():
    records = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            config = get_realistic_bet_and_denom()
            records.append({
                "family": fam,
                "slot": slot,
                "volatility": np.random.choice(["Med", "Med-High", "High"]),
                "base_rvi": round(float(np.random.uniform(7.5, 9.5)), 2),
                "opt_denom": config["denom"],
                "opt_bet": config["bet"]
            })
    return records

# Initialize Session State Variables
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
            "Date": "8/28/2026",
            "Day": "Friday",
            "Family": "Cash Horns",
            "Slot": "Cleopatra’s Kingdom",
            "Spin of Feature Hit": 32,
            "Feature Type": "Free Spins",
            "Win Amount": 180.0,
            "Win Multiplier": 72.0,
            "Hit Number": 1,
            "Attempt Number": 1
        }
    ])

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = [
        {"role": "assistant", "content": "Welcome! I am your AI Slot Execution Agent. Ask me for real-time recommendations, exit evaluations, or multi-phase spin execution plans."}
    ]

# Helper Functions
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

# Quick Action 1: Generates Multi-Phase Plan & Forces Focus to Agent Chat Tab
if st.sidebar.button("⚡ Suggest 3 Best Available Slots"):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x["base_rvi"], reverse=True)[:3]
    
    agent_msg = "### 🎯 Dynamic Multi-Phase Slot Plans:\n\n"
    for idx, item in enumerate(sorted_avail, 1):
        p1_bet = item['opt_bet']
        p2_bet = 1.25 if p1_bet in [2.50, 3.75] else (2.50 if p1_bet == 5.00 else 1.00)
        
        agent_msg += f"#### **{idx}. {item['slot']}** ({item['family']})\n"
        agent_msg += f"- **Check-In Config:** Denom: **{item['opt_denom']}** | Initial Bet: **${p1_bet:.2f}**\n"
        agent_msg += f"- **Phase 1 (Spins 1–20):** Probe at **${p1_bet:.2f}** bet on max lines.\n"
        agent_msg += f"- **Phase 2 (Spins 21–35):** If no feature by spin 20, step down bet to **${p2_bet:.2f}** on max lines.\n"
        agent_msg += f"- **Phase 3 (Exit/Spike Rule):** Hard exit if cold at spin 35. If feature hits >50x win, lock 80% profit & execute **8 Backup Spins** at **${p2_bet:.2f}**.\n\n"
    
    st.session_state.chat_messages.append({"role": "user", "content": "Suggest the 3 best available slots right now with step-by-step execution plans."})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# Quick Action 2: Generates Re-Trigger Evaluation & Forces Focus to Agent Chat Tab
if st.sidebar.button("❓ Should I Repeat / Re-Trigger?"):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll
    
    user_q = f"Should I repeat/re-trigger on my current machine? Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}, Current Bankroll: ${st.session_state.current_bankroll:.2f}, Target: ${st.session_state.session_target:.2f}."
    
    if diff > 0 and target_dist <= 200:
        evaluation = f"🎯 **Target Near (${target_dist:.2f} remaining)!**\n\n- **Phase 1:** Reduce bet to $1.25 / $2.50 max lines.\n- **Phase 2:** Execute **5–10 Backup Spins** max.\n- **Phase 3:** Cash out immediately once target is hit or after 10 spins."
    elif diff > 300:
        evaluation = f"🔥 **Big Win Active (+${diff:.2f})!**\n\n- **Phase 1:** Immediately pocket & lock 80% of win.\n- **Phase 2:** Execute **8 Backup Spins** at step-down bet ($2.50).\n- **Phase 3:** If no cluster re-trigger occurs within 8 spins, hard exit."
    elif diff < -250:
        evaluation = f"⚠️ **Cold Cycle Detected (-${abs(diff):.2f})!**\n\n- **Phase 1:** Step down bet to $1.00/$1.25 for 15 spins to preserve bankroll.\n- **Phase 2:** If feature triggers, recover loss and exit immediately.\n- **Phase 3:** If still cold after 15 spins, exit to a fresh machine."
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

# Programmatic Tab Selector using Radio Buttons
selected_tab = st.radio(
    "Navigation Tabs",
    options=TAB_OPTIONS,
    index=TAB_OPTIONS.index(st.session_state.active_tab),
    horizontal=True,
    label_visibility="collapsed"
)

# Keep Session State Synced with Radio Button Selection
if selected_tab != st.session_state.active_tab:
    st.session_state.active_tab = selected_tab

st.markdown("---")

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
if st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board (Feature-RVI Strategy Matrix)")
    st.caption("30 top-ranked slots with realistic max-line bet configurations.")
    
    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_priority = sorted(available_slots, key=lambda x: x["base_rvi"], reverse=True)
    current_display = sorted_priority[:st.session_state.display_limit]
    
    table_data = []
    for rank, item in enumerate(current_display, 1):
        eval_spins = int(max(35, round(item["base_rvi"] * 4.5)))
        table_data.append({
            "Rank": rank,
            "Slot Family": item["family"],
            "Slot Theme Name": item["slot"],
            "Recommended Denom": item["opt_denom"],
            "Optimal Bet ($)": f"${item['opt_bet']:.2f}",
            "Min Eval Spin Window": eval_spins,
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
    st.caption("Cascading Family $\\rightarrow$ Slot Theme selection for instant step-by-step game plans.")
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        card_family = st.selectbox("1. Select Slot Family:", options=list(SLOT_MASTER_LIST.keys()), key="card_fam_select")
    with col_c2:
        available_card_slots = SLOT_MASTER_LIST[card_family]
        card_slot = st.selectbox("2. Select Slot Theme:", options=available_card_slots, key="card_slot_select")

    if card_slot:
        slot_data = next((s for s in st.session_state.slots_db if s["slot"] == card_slot), None)
        if slot_data:
            st.markdown("---")
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-In Denom", slot_data["opt_denom"])
            c2.metric("Base Bet Size", f"${slot_data['opt_bet']:.2f}")
            c3.metric("Min Evaluation Window", "35–40 Spins Floor")
            c4.metric("Line Win Offset Buffer", "10% – 15%")
            
            p1_bet = slot_data['opt_bet']
            p2_bet = 1.25 if p1_bet in [2.50, 3.75] else (2.50 if p1_bet == 5.00 else 1.00)

            st.markdown("---")
            st.markdown("#### 🔄 Step-by-Step Multi-Phase Execution Plan")
            st.write(f"**Phase 1: Initial Probe (Spins 1 – 20)**")
            st.write(f"- Set machine to **{slot_data['opt_denom']}** denomination at **${p1_bet:.2f}** bet on max lines.")
            st.write(f"- Play 20 full spins. Line hits automatically buffer bankroll decay.")
            
            st.markdown("---")
            st.write(f"**Phase 2: Tiered Bet Step-Down (Spins 21 – 35 if No Feature)**")
            st.write(f"- If no feature triggers by spin 20, **step down bet to ${p2_bet:.2f}** on max lines.")
            st.write(f"- Continue probing for 15 additional spins.")
            
            st.markdown("---")
            st.write(f"**Phase 3: Spike, Backup Spin & Exit Rule**")
            st.write(f"- **Cold Machine:** If no feature by spin 35, hard exit to next priority slot.")
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
    st.caption("Matches your exact Google Sheet schema. Zone is omitted so sheet formulas calculate it automatically.")
    
    with st.form("exact_gs_entry_form", clear_on_submit=True):
        col_e1, col_e2, col_e3 = st.columns(3)
        
        with col_e1:
            default_date = datetime.now().strftime("%m/%d/%Y")
            default_day = datetime.now().strftime("%A")
            
            entry_date = st.text_input("Date (e.g. 8/28/2026):", value=default_date)
            entry_day = st.text_input("Day (e.g. Friday):", value=default_day)
            
            entry_family = st.selectbox("Slot Family:", list(SLOT_MASTER_LIST.keys()))
            entry_slot = st.selectbox("Slot Theme Name:", SLOT_MASTER_LIST[entry_family])

        with col_e2:
            entry_spin_hit = st.number_input("Spin of Feature Hit:", min_value=1, max_value=500, value=32)
            entry_feat_type = st.selectbox("Feature Type:", ["Free Spins", "Hold & Spin", "Pick Bonus", "Progressive / Jackpot", "Cluster Win"])
            entry_win_amt = st.number_input("Win Amount ($):", min_value=0.0, value=150.0, step=10.0)

        with col_e3:
            entry_multiplier = st.number_input("Win Multiplier (x):", min_value=0.0, value=50.0, step=5.0)
            entry_hit_num = st.number_input("Hit Number:", min_value=1, max_value=20, value=1)
            entry_attempt_num = st.number_input("Attempt Number:", min_value=1, max_value=20, value=1)

        submit_gs_entry = st.form_submit_button("💾 Save Session Record to Dataset")
        
        if submit_gs_entry:
            new_gs_log = {
                "Date": entry_date,
                "Day": entry_day,
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
            
            st.success(f"Logged entry for '{entry_slot}'! Win Amount: ${entry_win_amt:.2f} ({entry_multiplier}x).")

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
    st.caption("Chat with the strategy agent in real-time for multi-phase plans, exit evaluations, or backup spin rules.")
    
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            
    if prompt := st.chat_input("Ask a strategy question (e.g. 'Give me a 3-phase plan for Dragon Link Golden Century'):"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
            
        reply = f"**Strategy Guidance:** Based on active bankroll **${st.session_state.current_bankroll:.2f}** and target **${st.session_state.session_target:.2f}**:\n\n"
        reply += "1. **Phase 1 (Initial Probe):** Play 20 spins at base bet level ($2.50 or $5.00) on max lines.\n"
        reply += "2. **Phase 2 (Step-Down Probe):** If no feature hits by spin 20, step down bet to $1.25 / $2.50 on max lines for 15 additional spins.\n"
        reply += "3. **Phase 3 (Profit Lock & Backup Spins):** On major payout, lock 80% core profit and execute 8 backup spins at reduced bet before exiting."
            
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
    ### Bounding & Denomination Rules
    1. **5c Denom ($1.25 Minimum):** Max 25 lines requires $1.25 min bet. Allowed: $1.25, $2.50, $3.75, $5.00, $6.25, $7.50, $10.00.
    2. **10c Denom ($2.50 Minimum):** Allowed: $2.50, $5.00, $7.50, $10.00.
    3. **2c Denom (50 lines):** Allowed: $1.00, $2.00, $3.00, $5.00, $10.00. (No $2.50 on 2c).
    4. **Multi-Phase Execution Strategy:**
       - **Phase 1 (Probe):** 20 spins at target bet level on max lines.
       - **Phase 2 (Step-Down):** 15 spins at reduced bet if feature hasn't triggered.
       - **Phase 3 (Exit/Spike):** Hard exit after 35 cold spins, or execute 8 backup spins to harvest cluster re-triggers after a major hit.
    """)
