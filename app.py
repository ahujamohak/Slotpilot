import streamlit as st

# ==========================================
# 1. PAGE CONFIG & INITIAL SESSION STATE
# ==========================================
st.set_page_config(
    page_title="🎰 Slot Strategy Agent & Live Hub",
    page_icon="🎰",
    layout="wide"
)

# Initialize Session State Variables
if "session_start_bankroll" not in st.session_state:
    st.session_state.session_start_bankroll = 1000.0

if "current_bankroll" not in st.session_state:
    st.session_state.current_bankroll = 1000.0

if "session_target" not in st.session_state:
    st.session_state.session_target = 1500.0

if "played_basket" not in st.session_state:
    st.session_state.played_basket = []

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "🤖 Interactive Agent Chat"

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = [
        {
            "role": "assistant",
            "content": "👋 Welcome to your **Live Session Hub & Dynamic Slot Agent**. Use the sidebar to log quick events or consult me about machine choices, phase bet sizing, and pivot rules."
        }
    ]

# Default Machine Database with Volatility & Dynamic Phase Data
if "slots_db" not in st.session_state:
    st.session_state.slots_db = [
        {
            "slot": "Dragon Link - Golden Century",
            "family": "Dragon Link",
            "base_rvi": 88.5,
            "volatility": "High",
            "opt_bet": 5.00,
            "phase2_bet": 7.50,  # Dynamic Step UP
            "phase1_spins": 15,
            "phase2_spins": 10,
            "checkin_alloc": 150.00
        },
        {
            "slot": "Lightning Link - Sahara Gold",
            "family": "Lightning Link",
            "base_rvi": 84.0,
            "volatility": "Medium",
            "opt_bet": 2.50,
            "phase2_bet": 2.50,  # Dynamic Maintain
            "phase1_spins": 20,
            "phase2_spins": 15,
            "checkin_alloc": 100.00
        },
        {
            "slot": "Dragon Cash - Autumn Moon",
            "family": "Dragon Cash",
            "base_rvi": 81.2,
            "volatility": "Low",
            "opt_bet": 2.00,
            "phase2_bet": 1.00,  # Dynamic Step DOWN
            "phase1_spins": 25,
            "phase2_spins": 15,
            "checkin_alloc": 75.00
        },
        {
            "slot": "Dollar Storm - Emperor's Treasure",
            "family": "Dollar Storm",
            "base_rvi": 79.5,
            "volatility": "High",
            "opt_bet": 5.00,
            "phase2_bet": 2.50,  # Defensive Step DOWN
            "phase1_spins": 15,
            "phase2_spins": 10,
            "checkin_alloc": 100.00
        }
    ]


# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def get_top_unplayed_slot():
    """Returns the highest RVI machine that hasn't been played yet."""
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    if available:
        return sorted(available, key=lambda x: x.get("base_rvi", 0), reverse=True)[0]
    return None


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

# 1. SUGGEST 3 BEST AVAILABLE SLOTS
if st.sidebar.button("⚡ Suggest 3 Best Available Slots"):
    available = [s for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket]
    sorted_avail = sorted(available, key=lambda x: x.get("base_rvi", 0), reverse=True)[:3]
    
    if sorted_avail:
        agent_msg = "### 🎯 Dynamic Multi-Phase Slot Recommendations\n\n"
        for idx, item in enumerate(sorted_avail, 1):
            p1 = item.get('opt_bet', 5.0)
            p2 = item.get('phase2_bet', p1 * 0.5)
            alloc = item.get('checkin_alloc', 100.0)
            p1_spins = item.get('phase1_spins', 20)
            p2_spins = item.get('phase2_spins', 15)
            volatility = item.get('volatility', 'Medium')
            
            bet_action = "Step DOWN to" if p2 < p1 else ("Step UP to" if p2 > p1 else "Maintain")
            
            agent_msg += f"#### **{idx}. {item['slot']}** ({item.get('family', 'Standard')}) — *Volatility: {volatility}*\n"
            agent_msg += f"- **Check-In Allocation:** **${alloc:.2f}**\n"
            agent_msg += f"- **Phase 1 Probe:** {p1_spins} spins @ **${p1:.2f}**\n"
            agent_msg += f"- **Phase 2 Adjustment:** {p2_spins} spins — {bet_action} **${p2:.2f}**\n"
            agent_msg += f"- **Exit/Spike Rule:** Exit if cold after Phase 2. On hit >50x, execute **8 Backup Spins** @ **${p2:.2f}** & lock profit.\n\n"
    else:
        agent_msg = "⚠️ **All registered machines have been played in this session.** Reset played basket or add fresh slot profiles."

    st.session_state.chat_messages.append({"role": "user", "content": "Suggest the 3 best available slots right now with check-in allocations and dynamic phase strategy."})
    st.session_state.chat_messages.append({"role": "assistant", "content": agent_msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 2. MACHINE RE-TRIGGER & REPEAT ADVISOR
if st.sidebar.button("❓ Should I Repeat / Re-Trigger?"):
    diff = st.session_state.current_bankroll - st.session_state.session_start_bankroll
    target_dist = st.session_state.session_target - st.session_state.current_bankroll
    
    user_q = f"Should I repeat/re-trigger on my current machine? Starting: ${st.session_state.session_start_bankroll:.2f}, Current: ${st.session_state.current_bankroll:.2f}, Target: ${st.session_state.session_target:.2f}."
    
    if diff > 0 and target_dist <= 200:
        evaluation = f"🎯 **Target Near (${target_dist:.2f} remaining)!**\n\n"
        evaluation += f"- **Check-In Cap:** Allocate max $50-$100 from profit.\n"
        evaluation += f"- **Phase Strategy:** Execute defensive **5–8 Backup Spins** at minimum base bet.\n"
        evaluation += f"- **Exit Rule:** Cash out instantly once target standard balance hits."
    elif diff > 300:
        evaluation = f"🔥 **Big Win Active (+${diff:.2f} Profit)!**\n\n"
        evaluation += f"- **Profit Protection:** Lock 80% of win into core balance.\n"
        evaluation += f"- **Phase Strategy:** Execute **8 Backup Spins**. If volatility is high, hold bet size; if low, step down 50%.\n"
        evaluation += f"- **Exit Rule:** Hard exit if no feature re-triggers within backup window."
    elif diff < -250:
        evaluation = f"⚠️ **Cold Cycle Detected (-${abs(diff):.2f})!**\n\n"
        evaluation += f"- **Check-In Cap:** Preserve core capital.\n"
        evaluation += f"- **Phase Strategy:** Do NOT re-trigger high bets. Drop to lowest baseline tier or pivot immediately.\n"
        evaluation += f"- **Exit Rule:** Exit to fresh candidate if next 10 spins yield zero momentum."
    else:
        evaluation = "✅ **Standard Operational State.** Continue dynamic phase probe cycle based on current machine volatility."
        
    st.session_state.chat_messages.append({"role": "user", "content": user_q})
    st.session_state.chat_messages.append({"role": "assistant", "content": f"### 📊 Dynamic Re-Trigger & Exit Strategy\n\n{evaluation}"})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 3. LOCK-IN PROFIT PLAN
if st.sidebar.button("🔒 Big Win Profit Lock Plan"):
    cur_br = st.session_state.current_bankroll
    start_br = st.session_state.session_start_bankroll
    profit = cur_br - start_br
    
    if profit > 0:
        cash_out = start_br + (profit * 0.80)
        play_money = profit * 0.20
        msg = f"### 💰 Profit Lock & Risk-Free Execution Plan\n\n"
        msg += f"- **Current Bankroll:** ${cur_br:,.2f} (+${profit:,.2f} Net Profit)\n"
        msg += f"- **💵 Cash Out Now (Secured Profit):** **${cash_out:,.2f}**\n"
        msg += f"- **🎰 Risk-Free Play Buffer:** **${play_money:,.2f}**\n\n"
        msg += f"**Execution Steps:**\n"
        msg += f"1. Run **8 Backup Spins** on active machine using Phase 2 bet size.\n"
        msg += f"2. Allocate remaining buffer (${play_money:,.2f}) to top-ranked unplayed machine.\n"
        msg += f"3. **Strict Floor Rule:** Banked ${cash_out:,.2f} is non-negotiable and cannot be re-inserted."
    else:
        msg = "### ℹ️ No Profit Buffer Active\nBankroll is flat or at a loss relative to start. Focus on standard probing before applying lock-in protocols."

    st.session_state.chat_messages.append({"role": "user", "content": "I hit a major win! Give me my exact profit lock-in numbers and exit steps."})
    st.session_state.chat_messages.append({"role": "assistant", "content": msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 4. STOP LOSS & CIRCUIT BREAKER
if st.sidebar.button("🛑 Check Circuit Breaker / Stop Loss"):
    cur_br = st.session_state.current_bankroll
    start_br = st.session_state.session_start_bankroll
    loss = start_br - cur_br
    
    if loss >= 200:
        msg = f"### 🚨 CIRCUIT BREAKER TRIGGERED\n\n"
        msg += f"- **Current Loss:** -${loss:,.2f} from starting capital.\n"
        msg += f"- **Mandatory Action:** Step away for a **15-minute floor walk / break**.\n"
        msg += f"- **Bet Floor Restriction:** Cap maximum bet size at lowest baseline tier for next 2 machines.\n"
        msg += f"- **Hard Session Stop:** End session completely if total bankroll touches **${start_br * 0.5:,.2f}**."
    else:
        msg = f"### ✅ Risk Status: Operational\n\nCurrent session variance (-${max(0, loss):,.2f}) is within acceptable parameters. Continue standard execution."

    st.session_state.chat_messages.append({"role": "user", "content": "Check my circuit breaker and risk thresholds."})
    st.session_state.chat_messages.append({"role": "assistant", "content": msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 5. DYNAMIC PHASE 2 GUIDE
if st.sidebar.button("📉 Dynamic Phase 2 Transition Guide"):
    msg = "### 🔄 Dynamic Phase 2 Transition Matrix\n\n"
    msg += "Phase 2 bet adjustments depend on machine volatility and Phase 1 feedback (not hardcoded step-downs):\n\n"
    msg += "| Machine Profile | Phase 1 State | Recommended Phase 2 Bet Action |\n"
    msg += "| :--- | :--- | :--- |\n"
    msg += "| **High Volatility / High RVI** | Cold Probe | **Step UP 25-50%** (Trigger chase window) |\n"
    msg += "| **Medium Volatility** | Partial Hits | **Maintain Base Bet** (Extend spin count) |\n"
    msg += "| **Low Volatility / Standard** | Completely Cold | **Step DOWN 50%** (Capital defense) |\n"
    msg += "| **Post-Feature Spike** | High Return (>50x) | **Step DOWN to Backup Tier** (8-spin exit check) |\n\n"
    msg += "👉 *Ask the chat agent with your current spin count and machine name for exact dollar figures.*"

    st.session_state.chat_messages.append({"role": "user", "content": "How should I handle Phase 2 bet sizing for my active machine?"})
    st.session_state.chat_messages.append({"role": "assistant", "content": msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 6. TOP CANDIDATE CHECK-IN CALCULATOR
if st.sidebar.button("💰 Calculate Next Machine Check-In"):
    next_slot = get_top_unplayed_slot()
    if next_slot:
        p1 = next_slot.get('opt_bet', 5.0)
        p2 = next_slot.get('phase2_bet', p1 * 0.5)
        p1_spins = next_slot.get('phase1_spins', 20)
        p2_spins = next_slot.get('phase2_spins', 15)
        alloc = next_slot.get('checkin_alloc', 100.0)
        
        raw_cost = (p1_spins * p1) + (p2_spins * p2)
        bet_trend = "Increase" if p2 > p1 else ("Decrease" if p2 < p1 else "Maintain")
        
        msg = f"### 💵 Next Machine Profile & Capital Required\n\n"
        msg += f"**Target Machine:** **{next_slot['slot']}** ({next_slot.get('family', 'Standard')})\n\n"
        msg += f"- **Phase 1 ({p1_spins} spins @ ${p1:.2f}):** ${p1_spins * p1:.2f}\n"
        msg += f"- **Phase 2 ({p2_spins} spins @ ${p2:.2f} — {bet_trend}):** ${p2_spins * p2:.2f}\n"
        msg += f"- **Calculated Session Budget:** ${raw_cost:.2f}\n"
        msg += f"- **💵 Cash Check-In (Rounded):** **${alloc:.2f}**\n"
    else:
        msg = "⚠️ All candidate machines in your database have already been logged for this session."

    st.session_state.chat_messages.append({"role": "user", "content": "Calculate cash check-in and phase parameters for the top unplayed machine."})
    st.session_state.chat_messages.append({"role": "assistant", "content": msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()

# 7. ANY-SPIN PIVOT ADVISOR
if st.sidebar.button("🔄 Pivot or Stay Advisor"):
    next_slot = get_top_unplayed_slot()
    next_up = next_slot["slot"] if next_slot else "Fresh Unplayed Machine"
    
    msg = f"### 🔄 Any-Spin Pivot & Exit Rules\n\n"
    msg += f"You can evaluate a machine pivot at **any spin state** during your session:\n\n"
    msg += f"- **STAY IF:**\n"
    msg += f"  - You are in Phase 1 and seeing frequent line hits, orb holds, or mini-features.\n"
    msg += f"  - You hit a feature within the last 8 spins and are executing backup spins.\n"
    msg += f"  - Active bankroll is trending upward over the last 10 spins.\n\n"
    msg += f"- **PIVOT IF:**\n"
    msg += f"  - You completed your Phase 1 & Phase 2 windows with zero feature triggers.\n"
    msg += f"  - You finished 8 post-hit backup spins without a re-trigger.\n"
    msg += f"  - Machine went 12+ consecutive spins without a single line win.\n\n"
    msg += f"👉 **Recommended Pivot Destination:** Move to **{next_up}**."

    st.session_state.chat_messages.append({"role": "user", "content": "Should I pivot to a new machine right now or stay on my active machine?"})
    st.session_state.chat_messages.append({"role": "assistant", "content": msg})
    st.session_state.active_tab = "🤖 Interactive Agent Chat"
    st.rerun()


# ==========================================
# 4. MAIN APP BODY & WORKSPACE
# ==========================================
st.title("🎰 Real-Time Slot Strategy & Session Hub")

# Quick Session Overview Metric Bar
col_m1, col_m2, col_m3, col_m4 = st.columns(4)
col_m1.metric("Starting Bankroll", f"${st.session_state.session_start_bankroll:,.2f}")
col_m2.metric("Current Bankroll", f"${st.session_state.current_bankroll:,.2f}", delta=f"{st.session_state.current_bankroll - st.session_state.session_start_bankroll:,.2f}")
col_m3.metric("Target Goal", f"${st.session_state.session_target:,.2f}")
col_m4.metric("Played Machines", f"{len(st.session_state.played_basket)}")

st.markdown("---")

# Navigation Tabs
tabs = ["🤖 Interactive Agent Chat", "📊 Active Machines Database"]
active_tab_index = 0 if st.session_state.active_tab == "🤖 Interactive Agent Chat" else 1

selected_tab = st.radio("Workspace View", tabs, index=active_tab_index, horizontal=True)
st.session_state.active_tab = selected_tab

if selected_tab == "🤖 Interactive Agent Chat":
    st.subheader("🤖 Strategy Agent Consultation")
    
    # Display Chat History
    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # User Chat Input
    if user_input := st.chat_input("Ask about phase bets, check-in amounts, or pivot rules..."):
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Dynamic Response Handler
        top_slot = get_top_unplayed_slot()
        top_name = top_slot["slot"] if top_slot else "a high-RVI machine"
        
        reply = f"Acknowledged. Based on your current bankroll of **${st.session_state.current_bankroll:,.2f}**, keep strict adherence to your phase windows. If evaluating a new play, consider **{top_name}** for your next check-in."

        st.session_state.chat_messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)

elif selected_tab == "📊 Active Machines Database":
    st.subheader("📊 Session Candidate Database")
    
    # Render Machine Data Table
    st.dataframe(st.session_state.slots_db, use_container_width=True)
    
    # Machine Log Management
    st.subheader("Mark Played Machine")
    played_choice = st.selectbox("Select machine to mark as played:", [s["slot"] for s in st.session_state.slots_db if s["slot"] not in st.session_state.played_basket])
    if st.button("Log to Played Basket"):
        if played_choice:
            st.session_state.played_basket.append(played_choice)
            st.success(f"Added {played_choice} to played basket.")
            st.rerun()
