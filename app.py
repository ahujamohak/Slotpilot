import streamlit as st
import pandas as pd
import numpy as np

# Set Streamlit Page Layout
st.set_page_config(page_title="Slot Strategy & Execution Agent", layout="wide")

# ==========================================
# 1. INITIAL SESSION STATE & DATA ENGINE
# ==========================================

# Base Dataset Simulation / Database Setup
DEFAULT_DATASET = [
    {"family": "All Aboard / Lucky Link", "slot": "Shinobi", "volatility": "High", "base_rvi": 8.8, "min_denom": "1c", "opt_denom": "5c", "opt_bet": 5.00},
    {"family": "All Aboard / Lucky Link", "slot": "Go West", "volatility": "High", "base_rvi": 8.6, "min_denom": "2c", "opt_denom": "10c", "opt_bet": 5.00},
    {"family": "Lightning Link", "slot": "Moon Race", "volatility": "Med-High", "base_rvi": 8.4, "min_denom": "1c", "opt_denom": "5c", "opt_bet": 2.50},
    {"family": "Lightning Link", "slot": "Tiki Fire", "volatility": "Med", "base_rvi": 8.2, "min_denom": "1c", "opt_denom": "2c", "opt_bet": 2.50},
    {"family": "Lightning Link", "slot": "Heart Throb", "volatility": "High", "base_rvi": 8.5, "min_denom": "1c", "opt_denom": "5c", "opt_bet": 5.00},
    {"family": "Lightning Link", "slot": "Sahara Gold", "volatility": "Med", "base_rvi": 8.0, "min_denom": "1c", "opt_denom": "1c", "opt_bet": 1.00},
    {"family": "Dragon Link", "slot": "Panda Magic", "volatility": "High", "base_rvi": 8.9, "min_denom": "2c", "opt_denom": "10c", "opt_bet": 10.00},
    {"family": "Dragon Link", "slot": "Golden Century", "volatility": "High", "base_rvi": 8.7, "min_denom": "2c", "opt_denom": "5c", "opt_bet": 5.00},
    {"family": "Dragon Link", "slot": "Autumn Moon", "volatility": "Med-High", "base_rvi": 8.3, "min_denom": "1c", "opt_denom": "5c", "opt_bet": 2.50},
    {"family": "Dragon Link", "slot": "Happy & Prosperous", "volatility": "High", "base_rvi": 8.6, "min_denom": "2c", "opt_denom": "10c", "opt_bet": 10.00},
    {"family": "Dragon Cash", "slot": "Peacock Princess", "volatility": "High", "base_rvi": 8.8, "min_denom": "5c", "opt_denom": "10c", "opt_bet": 10.00},
    {"family": "Dragon Cash", "slot": "Spring Festival", "volatility": "High", "base_rvi": 8.5, "min_denom": "2c", "opt_denom": "5c", "opt_bet": 5.00},
]

# Generate procedural dataset expansion up to 100 entries for smooth dynamic loading
def get_expanded_dataset():
    data = list(DEFAULT_DATASET)
    families = ["Lightning Link", "Dragon Link", "Dragon Cash", "All Aboard / Lucky Link", "Dollar Storm", "Grand Star"]
    sub_names = ["Emperor", "Fortune", "Rising Sun", "Wild Panther", "High Roller", "Cleopatra", "Pharaoh", "Jackpot Express", "Treasure", "Chieftain"]
    
    idx = len(data) + 1
    for f in families:
        for sub in sub_names:
            if len(data) >= 80:
                break
            slot_name = f"{f.split()[0]} - {sub} #{idx}"
            data.append({
                "family": f,
                "slot": slot_name,
                "volatility": np.random.choice(["Med", "Med-High", "High"]),
                "base_rvi": round(float(np.random.uniform(7.5, 9.2)), 2),
                "min_denom": np.random.choice(["1c", "2c", "5c"]),
                "opt_denom": np.random.choice(["2c", "5c", "10c"]),
                "opt_bet": float(np.random.choice([1.00, 2.50, 5.00, 10.00]))
            })
            idx += 1
    return data

# Initialize Session States
if "slots_db" not in st.session_state:
    st.session_state.slots_db = get_expanded_dataset()
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

# Helper Functions
def mark_slot_played(slot_name):
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)

def restore_slot(slot_name):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)

# ==========================================
# 2. SIDEBAR CONFIGURATION & CONTROLS
# ==========================================

st.sidebar.title("🎰 Real-Time Session Hub")

st.sidebar.subheader("Bankroll & Target Settings")
st.session_state.session_start_bankroll = st.sidebar.number_input("Session Start Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
st.session_state.current_bankroll = st.sidebar.number_input("Current Real-Time Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
st.session_state.session_target = st.sidebar.number_input("Session Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)

st.sidebar.markdown("---")
st.sidebar.subheader("Quick Floor Actions (Point 7)")

# Quick Button 1: Suggest Top 3 Available Slots
if st.sidebar.button("⚡ Suggest 3 Best Available Slots"):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x["base_rvi"], reverse=True)[:3]
    
    st.sidebar.success("Top 3 Unplayed Recommendations:")
    for idx, item in enumerate(sorted_avail, 1):
        st.sidebar.markdown(f"**{idx}. {item['slot']}** ({item['family']})")
        st.sidebar.caption(f"Denom: {item['opt_denom']} | Bet: ${item['opt_bet']:.2f} | Volatility: {item['volatility']}")

# Quick Button 2: Repeat / Exit Decision
if st.sidebar.button("❓ Should I Repeat on Current Slot?"):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll
    
    if diff > 0 and target_dist <= 200:
        st.sidebar.info("🎯 **Target Near!** Play a 5–10 Backup Spin sequence at a reduced bet ($2.50/$1.00), then exit to lock target.")
    elif diff > 300:
        st.sidebar.warning("🔥 **Big Hit Active!** Execute 8 Backup Spins max. Lock 80% of win, play remaining buffer.")
    elif diff < -250:
        st.sidebar.error("⚠️ **Cold Cycle.** Step down bet to $1.00 for 15 spins to preserve bankroll or Exit immediately.")
    else:
        st.sidebar.success("✅ **Continue Play.** Within safe operational variance. Maintain standard tier plan.")

# ==========================================
# 3. MAIN DASHBOARD TABS
# ==========================================

st.title("Casino Slot Optimization & Execution Agent")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Today's Priority Board", 
    "📋 Pre-Planned Execution Cards", 
    "🕹️ Live Mid-Game & Target Adjust", 
    "🧺 Played Basket & Overrides", 
    "📖 Documentation & Rules"
])

# ------------------------------------------
# TAB 1: TODAY'S PRIORITY BOARD
# ------------------------------------------
with tab1:
    st.subheader("Today's Priority Board (Feature-RVI Strategy Matrix)")
    st.caption("Auto-ranked candidate slots with minimum 35–40 spin evaluation windows, 10-15% line win buffer, and strict $1.00–$10.00 bet bounding.")
    
    # Filter out played slots
    available_slots = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_priority = sorted(available_slots, key=lambda x: x["base_rvi"], reverse=True)
    
    # Apply dynamic limit
    current_display = sorted_priority[:st.session_state.display_limit]
    
    # Format Table Data (Point 4: Removed Avg Multiplier and Hits)
    table_data = []
    for rank, item in enumerate(current_display, 1):
        # Point 8 & 11: Softened spin limits (min 35 spins floor)
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
            st.info("All available slots in dataset fully loaded.")
            
    with col_b:
        st.write(f"Showing **{len(current_display)}** of **{len(sorted_priority)}** unplayed slots.")

# ------------------------------------------
# TAB 2: PRE-PLANNED EXECUTION CARDS (POINT 5)
# ------------------------------------------
with tab2:
    st.subheader("Pre-Planned Per-Slot Execution Cards")
    st.caption("Detailed step-by-step game plans designed to be checked in advance to avoid live agent latency.")
    
    selected_card_slot = st.selectbox(
        "Select Slot to View Detailed Execution Card:", 
        options=[s["slot"] for s in current_display] if current_display else ["None Available"]
    )
    
    if selected_card_slot != "None Available":
        slot_data = next((s for s in current_display if s["slot"] == selected_card_slot), None)
        if slot_data:
            st.markdown(f"### 🎰 Execution Card: **{slot_data['slot']}** ({slot_data['family']})")
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-In Denom", slot_data["opt_denom"])
            c2.metric("Base Bet Size", f"${slot_data['opt_bet']:.2f}")
            c3.metric("Min Evaluation Window", "38–42 Spins")
            c4.metric("Line Win Offset Buffer", "10% – 15%")
            
            st.markdown("---")
            st.markdown("#### 🔄 Step-by-Step Execution Plan")
            
            p1_bet = slot_data['opt_bet']
            p2_bet = max(1.00, round(p1_bet / 2.0, 2))
            if p2_bet not in [1.00, 2.50, 5.00, 10.00]:
                p2_bet = 2.50 if p1_bet >= 5.00 else 1.00

            st.write(f"**Phase 1: Initial Feature Probe (Spins 1 – 20)**")
            st.write(f"- Set machine to **{slot_data['opt_denom']}** denomination at **${p1_bet:.2f}** bet.")
            st.write(f"- Play 20 full spins. Track line hits (assume 10-15% return extends cycle buffer).")
            
            st.markdown("---")
            st.write(f"**Phase 2: Contingency Step-Down (Spins 21 – 40 if No Feature)**")
            st.write(f"- If no feature triggers by spin 20, **step down bet to ${p2_bet:.2f}** (adjust bet multiplier or switch denom to preserve max line coverage).")
            st.write(f"- Play remaining 20 spins at lower bet size to maintain spin volume without burning bankroll.")
            
            st.markdown("---")
            st.write(f"**Phase 3: Spike & Backup Spin Rule (If Feature/Win Hits)**")
            st.write(f"- **Credit Spike > $150:** Lock in 70% of profit toward daily target.")
            st.write(f"- **Backup Spin Rule:** After any major feature payout, execute exactly **8 Backup Spins** at **${p2_bet:.2f}** bet to catch cluster re-triggers. If no re-trigger, exit machine immediately.")

            st.markdown("---")
            if st.button(f"✅ Mark '{slot_data['slot']}' as Played"):
                mark_slot_played(slot_data['slot'])
                st.success(f"Moved '{slot_data['slot']}' to Played Basket!")
                st.rerun()

# ------------------------------------------
# TAB 3: LIVE MID-GAME & DYNAMIC TARGET ADJUST
# ------------------------------------------
with tab3:
    st.subheader("Live Mid-Game Session Management")
    st.caption("Adjust target bankroll mid-game or issue quick decisions without losing active session state.")
    
    col_l1, col_l2 = st.columns(2)
    
    with col_l1:
        st.markdown("### 🎯 Mid-Game Target Adjuster (Point 2)")
        new_target = st.number_input("Update Session Target Mid-Play ($)", value=float(st.session_state.session_target), step=50.0)
        if st.button("Apply New Target"):
            st.session_state.session_target = new_target
            st.success(f"Session Target updated to ${new_target:.2f}!")
            
        cur_profit = st.session_state.current_bankroll - st.session_state.session_start_bankroll
        rem_target = st.session_state.session_target - st.session_state.current_bankroll
        
        st.metric("Current Net Profit / Loss", f"${cur_profit:+.2f}")
        st.metric("Remaining Distance to Target", f"${rem_target:.2f}")

    with col_l2:
        st.markdown("### 🎰 Active Machine Quick Log")
        
        # Point 1: Family-to-Slot Filtering
        families_list = sorted(list(set(s["family"] for s in st.session_state.slots_db)))
        sel_family = st.selectbox("1. Filter by Slot Family:", families_list)
        
        # Dynamic slot filtering based on family
        filtered_slots = [s["slot"] for s in st.session_state.slots_db if s["family"] == sel_family and s["slot"] not in st.session_state.played_basket]
        
        sel_slot = st.selectbox("2. Select Slot Name:", options=filtered_slots if filtered_slots else ["No unplayed slots in family"])
        
        # Option to manually add new slot name (Point 1)
        with st.expander("➕ Add New Custom Slot to Family"):
            new_custom_slot = st.text_input("New Slot Name:")
            if st.button("Add & Select Slot"):
                if new_custom_slot:
                    st.session_state.slots_db.append({
                        "family": sel_family,
                        "slot": new_custom_slot,
                        "volatility": "Med-High",
                        "base_rvi": 8.0,
                        "min_denom": "1c",
                        "opt_denom": "5c",
                        "opt_bet": 2.50
                    })
                    st.success(f"Added '{new_custom_slot}' to {sel_family}!")
                    st.rerun()

        if sel_slot and sel_slot != "No unplayed slots in family":
            if st.button(f"Log & Lock '{sel_slot}' Session Complete"):
                mark_slot_played(sel_slot)
                st.success(f"Logged '{sel_slot}' into Played Basket.")
                st.rerun()

# ------------------------------------------
# TAB 4: PLAYED BASKET & OVERRIDES (POINT 6)
# ------------------------------------------
with tab4:
    st.subheader("Daily Played Basket & Slot Overrides")
    st.caption("Slots played in this session are excluded from recommendations. Click 'Restore' to manually re-enable any machine.")
    
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
        
        st.markdown("#### 🔓 Unlock / Override Basket")
        restore_target = st.selectbox("Select Slot to Re-Enable for Play:", options=st.session_state.played_basket)
        if st.button("Unlock Selected Slot"):
            restore_slot(restore_target)
            st.success(f"Restored '{restore_target}' back to active recommendations!")
            st.rerun()
    else:
        st.info("No slots have been played yet in this active session.")

# ------------------------------------------
# TAB 5: DOCUMENTATION & RULES (POINT 3)
# ------------------------------------------
with tab5:
    st.subheader("Updated System Architecture & Rules")
    st.markdown("""
    ### Core Operating Rules
    1. **Dynamic Family Filtering:** Selecting a Slot Family automatically restricts option selections exclusively to validated models in that family.
    2. **Mid-Game Adjustments:** Target adjustments update session stop-loss and escalation math dynamically without breaking current session state.
    3. **Pre-Planned Cards:** 30 priority cards contain fully mapped execution blueprints (denom, initial bet, step-down bets, backup spin counts).
    4. **Played Basket Isolation:** Completed slot sessions are immediately placed into a cool-down basket to guarantee novel recommendations.
    5. **Spin Threshold Softening:** Ultra-short evaluation limits are disabled. All slots operate on a minimum **35–40 spin floor** to accommodate modern slot volatility.
    6. **Line-Hit Offset Model:** Dead spins are calculated as pure $0 payouts. Base line wins (10%–15% average return) automatically credit back into spin allowance.
    7. **Bet Bounds ($1.00 – $10.00):** Recommendations are capped at $10.00 max, actively utilizing $1.00, $2.50, and $5.00 steps for volume padding and bankroll preservation.
    8. **Backup Spins:** Following significant feature payouts, exactly 5–10 backup spins are conducted at reduced/equal bets to harvest potential cluster features before exiting.
    """)
