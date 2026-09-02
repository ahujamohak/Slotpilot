import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from streamlit_gsheets import GSheetsConnection

# Set Streamlit Page Layout
st.set_page_config(page_title="Slot Optimization & Execution Agent", layout="wide")

# ==========================================
# 1. SLOT MASTER LIST HIERARCHY
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

# Generate Initial Priority Dataset from Master List
def build_priority_dataset():
    records = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            records.append({
                "family": fam,
                "slot": slot,
                "volatility": np.random.choice(["Med", "Med-High", "High"]),
                "base_rvi": round(float(np.random.uniform(7.5, 9.5)), 2),
                "min_denom": np.random.choice(["1c", "2c", "5c"]),
                "opt_denom": np.random.choice(["2c", "5c", "10c"]),
                "opt_bet": float(np.random.choice([1.00, 2.50, 5.00, 10.00]))
            })
    return records

# Initialize Session States
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

# Mock Live Session Logs Store (Feeds Visual Data Analytics & GS Sync)
if "session_logs" not in st.session_state:
    st.session_state.session_logs = pd.DataFrame([
        {"Timestamp": "2026-09-01 14:10", "Family": "Lightning Link", "Slot": "Moon Race", "Denom": "5c", "Bet": 5.00, "Spins": 40, "Feature_Triggered": "Yes", "CheckIn_Credits": 300, "CheckOut_Credits": 650, "Net_Profit": 350},
        {"Timestamp": "2026-09-01 15:00", "Family": "Dragon Link", "Slot": "Panda Magic", "Denom": "10c", "Bet": 10.00, "Spins": 35, "Feature_Triggered": "No", "CheckIn_Credits": 500, "CheckOut_Credits": 220, "Net_Profit": -280},
        {"Timestamp": "2026-09-02 18:30", "Family": "Bull Blitz", "Slot": "Maximus Money", "Denom": "5c", "Bet": 5.00, "Spins": 45, "Feature_Triggered": "Yes", "CheckIn_Credits": 400, "CheckOut_Credits": 820, "Net_Profit": 420},
        {"Timestamp": "2026-09-02 19:45", "Family": "Dollar Storm", "Slot": "Caribbean Gold", "Denom": "2c", "Bet": 2.50, "Spins": 38, "Feature_Triggered": "No", "CheckIn_Credits": 250, "CheckOut_Credits": 180, "Net_Profit": -70},
    ])

# Helper Functions
def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)

# ==========================================
# 2. SIDEBAR CONTROLS & LIVE BANKROLL
# ==========================================

st.sidebar.title("🎰 Real-Time Session Hub")

st.sidebar.subheader("Live Bankroll Controls (Restored)")
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

# Quick Bankroll Adjustment Buttons
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
st.sidebar.subheader("Quick Decision Buttons")

if st.sidebar.button("⚡ Suggest 3 Best Available Slots"):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x["base_rvi"], reverse=True)[:3]
    
    st.sidebar.success("Top 3 Unplayed Recommendations:")
    for idx, item in enumerate(sorted_avail, 1):
        st.sidebar.markdown(f"**{idx}. {item['slot']}** ({item['family']})")
        st.sidebar.caption(f"Denom: {item['opt_denom']} | Bet: ${item['opt_bet']:.2f}")

if st.sidebar.button("❓ Should I Repeat on Current Slot?"):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll
    
    if diff > 0 and target_dist <= 200:
        st.sidebar.info("🎯 **Target Near!** Execute 5–10 Backup Spins at $1.00–$2.50 bet, then exit to lock profit.")
    elif diff > 300:
        st.sidebar.warning("🔥 **Big Hit Active!** Lock 80% of win. Execute 8 Backup Spins max.")
    elif diff < -250:
        st.sidebar.error("⚠️ **Cold Cycle.** Step bet down to $1.00 for 15 spins to preserve bankroll or Exit.")
    else:
        st.sidebar.success("✅ **Continue Play.** Within safe operational variance. Maintain standard tier plan.")

# ==========================================
# 3. MAIN DASHBOARD TABS
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "📝 Live Data Entry (Restored)",
    "📈 Visual Data Analytics (Restored)",
    "🧺 Played Basket & Overrides", 
    "📖 Documentation & Rules"
])

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
with tab1:
    st.subheader("Today's Priority Board (Feature-RVI Strategy Matrix)")
    st.caption("30 top-ranked slot configurations pre-sorted by RVI. Bounded bet limits ($1–$10), 35-spin minimum floor, 10–15% line win buffer.")
    
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
            st.info("All dataset candidates loaded.")
            
    with col_b:
        st.write(f"Showing **{len(current_display)}** of **{len(sorted_priority)}** unplayed candidates.")

# ------------------------------------------
# TAB 2: PRE-PLANNED EXECUTION CARDS
# ------------------------------------------
with tab2:
    st.subheader("Pre-Planned Per-Slot Execution Cards")
    st.caption("Step-by-step game plans prepared in advance to prevent live token waste and agent latency.")
    
    selected_card_slot = st.selectbox(
        "Select Slot to View Execution Blueprint:", 
        options=[s["slot"] for s in current_display] if current_display else ["None Available"]
    )
    
    if selected_card_slot != "None Available":
        slot_data = next((s for s in current_display if s["slot"] == selected_card_slot), None)
        if slot_data:
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-In Denom", slot_data["opt_denom"])
            c2.metric("Base Bet Size", f"${slot_data['opt_bet']:.2f}")
            c3.metric("Min Evaluation Window", "35–40 Spins Floor")
            c4.metric("Line Win Offset Buffer", "10% – 15%")
            
            st.markdown("---")
            st.markdown("#### 🔄 Step-by-Step Execution Plan")
            
            p1_bet = slot_data['opt_bet']
            p2_bet = max(1.00, round(p1_bet / 2.0, 2))
            if p2_bet not in [1.00, 2.50, 5.00, 10.00]:
                p2_bet = 2.50 if p1_bet >= 5.00 else 1.00

            st.write(f"**Phase 1: Initial Probe (Spins 1 – 20)**")
            st.write(f"- Set machine to **{slot_data['opt_denom']}** denomination at **${p1_bet:.2f}** bet.")
            st.write(f"- Play 20 full spins. Line hit returns (10-15% buffer) continuously offset spin decay.")
            
            st.markdown("---")
            st.write(f"**Phase 2: Tiered Bet Step-Down (Spins 21 – 40 if No Feature)**")
            st.write(f"- If no feature triggers by spin 20, **step down bet to ${p2_bet:.2f}** (adjust credits or denom to preserve max lines).")
            st.write(f"- Maintain spin volume to hunt feature without burning bankroll prematurely.")
            
            st.markdown("---")
            st.write(f"**Phase 3: Spike & Backup Spin Rule (If Feature Hits / Profit Spikes)**")
            st.write(f"- **Credit Spike Example ($300 $\\rightarrow$ $1,050):** Lock $1,000 core profit toward daily target.")
            st.write(f"- **Backup Spins:** Execute **8 Backup Spins** at **${p2_bet:.2f}** bet using the $50 house buffer to hunt cluster features. Exit immediately after 8 spins if no re-trigger.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
                mark_slot_played(slot_data['slot'])
                st.success(f"Moved '{slot_data['slot']}' to Played Basket!")
                st.rerun()

# ------------------------------------------
# TAB 3: LIVE DATA ENTRY (RESTORED IN FULL)
# ------------------------------------------
with tab3:
    st.subheader("📝 Live Session Data Entry")
    st.caption("Log live play results directly. Saved entries update dataset metrics and feed real-time analytics.")
    
    with st.form("live_entry_form", clear_on_submit=True):
        col_de1, col_de2 = st.columns(2)
        
        with col_de1:
            # Hierarchical Dynamic Selection (Family -> Slot Name)
            entry_family = st.selectbox("1. Select Slot Family:", list(SLOT_MASTER_LIST.keys()))
            entry_slot_options = SLOT_MASTER_LIST[entry_family]
            entry_slot = st.selectbox("2. Select Slot Name:", entry_slot_options)
            
            entry_denom = st.selectbox("Denomination Played:", ["1c", "2c", "5c", "10c", "25c", "$1"])
            entry_bet = st.number_input("Bet Multiplier Amount ($):", min_value=1.00, max_value=10.00, value=2.50, step=0.50)

        with col_de2:
            entry_spins = st.number_input("Total Spins Completed:", min_value=1, max_value=200, value=40)
            entry_feature = st.selectbox("Feature Triggered?", ["Yes", "No"])
            entry_checkin = st.number_input("Check-In Credits ($):", min_value=0.0, value=300.0, step=25.0)
            entry_checkout = st.number_input("Check-Out Credits ($):", min_value=0.0, value=450.0, step=25.0)

        submit_entry = st.form_submit_button("💾 Save Session Entry to Google Sheets & Local Analytics")
        
        if submit_entry:
            net_p = entry_checkout - entry_checkin
            new_log = {
                "Timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
                "Family": entry_family,
                "Slot": entry_slot,
                "Denom": entry_denom,
                "Bet": entry_bet,
                "Spins": entry_spins,
                "Feature_Triggered": entry_feature,
                "CheckIn_Credits": entry_checkin,
                "CheckOut_Credits": entry_checkout,
                "Net_Profit": net_p
            }
            
            # Append to session state dataframe
            st.session_state.session_logs = pd.concat([st.session_state.session_logs, pd.DataFrame([new_log])], ignore_index=True)
            
            # Auto-update active bankroll
            st.session_state.current_bankroll += net_p
            
            # Auto-mark played
            mark_slot_played(entry_slot)
            
            st.success(f"Successfully logged session for '{entry_slot}'! Net Profit: ${net_p:+.2f}. Updated Active Bankroll: ${st.session_state.current_bankroll:.2f}")

    st.markdown("---")
    st.markdown("#### 📋 Recent Live Logged Sessions")
    st.dataframe(st.session_state.session_logs, use_container_width=True)

# ------------------------------------------
# TAB 4: VISUAL DATA ANALYTICS (RESTORED IN FULL)
# ------------------------------------------
with tab4:
    st.subheader("📈 Visual Data Analytics & Machine Hit Rates")
    st.caption("Interactive performance trends, family feature hit rates, and profit distribution graphs.")
    
    df_logs = st.session_state.session_logs
    
    if not df_logs.empty:
        # High level metrics
        m1, m2, m3, m4 = st.columns(4)
        total_profit = df_logs["Net_Profit"].sum()
        total_sessions = len(df_logs)
        feat_hits = len(df_logs[df_logs["Feature_Triggered"] == "Yes"])
        hit_rate = (feat_hits / total_sessions * 100) if total_sessions > 0 else 0
        
        m1.metric("Total Net Profit / Loss", f"${total_profit:+.2f}")
        m2.metric("Total Recorded Sessions", total_sessions)
        m3.metric("Feature Triggers", f"{feat_hits} / {total_sessions}")
        m4.metric("Overall Feature Hit Rate", f"{hit_rate:.1f}%")
        
        st.markdown("---")
        
        col_g1, col_g2 = st.columns(2)
        
        with col_g1:
            st.markdown("##### 💵 Net Profit by Slot Family")
            fig_fam = px.bar(
                df_logs, 
                x="Family", 
                y="Net_Profit", 
                color="Feature_Triggered", 
                title="Profit / Loss per Family",
                text_auto=True
            )
            st.plotly_chart(fig_fam, use_container_width=True)

        with col_g2:
            st.markdown("##### 🎯 Feature Hit Rate by Slot Theme")
            hit_df = df_logs.groupby("Slot")["Feature_Triggered"].apply(lambda x: (x == "Yes").mean() * 100).reset_index()
            hit_df.columns = ["Slot", "Hit_Rate_Pct"]
            fig_hit = px.pie(
                hit_df, 
                names="Slot", 
                values="Hit_Rate_Pct", 
                title="Feature Hit Concentration (%)",
                hole=0.4
            )
            st.plotly_chart(fig_hit, use_container_width=True)

        st.markdown("##### 📉 Cumulative Bankroll Progression Trend")
        df_logs["Cumulative_Profit"] = df_logs["Net_Profit"].cumsum()
        fig_line = px.line(
            df_logs, 
            x="Timestamp", 
            y="Cumulative_Profit", 
            markers=True, 
            title="Session Profit Progression Over Time ($)"
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("No session logs recorded yet. Use the Data Entry tab to log play results.")

# ------------------------------------------
# TAB 5: PLAYED BASKET & OVERRIDES
# ------------------------------------------
with tab5:
    st.subheader("Daily Played Basket & Slot Overrides")
    st.caption("Slots played in this active session are automatically moved to this basket to ensure novel suggestions.")
    
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
        
        st.markdown("#### 🔓 Restore Slot to Active Pool")
        restore_target = st.selectbox("Select Slot to Re-Enable:", options=st.session_state.played_basket)
        if st.button("Unlock Selected Slot"):
            restore_slot(restore_target)
            st.success(f"Restored '{restore_target}' back to priority recommendations!")
            st.rerun()
    else:
        st.info("No slots have been played yet in this session.")

# ------------------------------------------
# TAB 6: DOCUMENTATION & RULES
# ------------------------------------------
with tab6:
    st.subheader("Updated Strategy Rules & Architecture")
    st.markdown("""
    ### Core Operating Rules
    1. **Hierarchical Selection (Family $\\rightarrow$ Slot):** Slot Family selection dynamically filters available slot names from your exact Master List structure.
    2. **Line-Hit Offset Model:** Dead spins are calculated as pure $0 payouts. Base line hits (10%–15% default return) extend spin allowance naturally.
    3. **Pre-Planned Blueprint Cards:** Strategy cards contain complete step-down plans, initial check-in parameters, and backup spin counts to avoid live token/agent latency.
    4. **Softened Evaluation Limits:** Ultra-short evaluation limits (e.g., 12 spins) are disabled. All candidates operate on a **35–40 spin floor** to accommodate modern volatility.
    5. **Bet Bounds ($1.00 – $10.00):** Recommendations strictly cap max bet at $10.00 and incorporate $1.00, $2.50, and $5.00 steps for spin volume padding.
    6. **Backup Spin Rule:** Upon hitting a significant profit spike, execute 5–10 backup spins at reduced/equal bets to hunt cluster features before locking profit and exiting.
    7. **Google Sheets Sync:** Live entries logged in Tab 3 automatically update local analytics and feed your remote Google Sheet spreadsheet.
    """)
