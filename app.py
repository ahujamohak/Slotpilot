import os
import re
import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from datetime import date
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

st.set_page_config(page_title="Slotpilot AI & Analytics", layout="wide", initial_sidebar_state="collapsed")

groq_api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")
client = Groq(api_key=groq_api_key) if groq_api_key else None

def get_worksheet():
    if "gcp_service_account" in st.secrets:
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
    else:
        gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("Slot Analysis High Bet")
    return sh.worksheet("Session Log")

@st.cache_data(ttl=30)
def load_data():
    worksheet = get_worksheet()
    records = worksheet.get_all_records()
    df = pd.DataFrame(records)
    
    numeric_cols = ["Spin of feature hit", "Win amount", "Win multiplier", "Hit Number", "Attempt Number"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            
    if "Date" in df.columns:
        df["Date_Parsed"] = pd.to_datetime(df["Date"], errors="coerce")
        
    return df

def safe_unique_options(series, default_list):
    if series is not None and not series.empty:
        vals = [str(x) for x in series.unique() if x != "" and x is not None]
        if vals:
            return sorted(vals)
    return default_list

def clean_thinking_tags(text: str) -> str:
    """Strips out <think>...</think> blocks or unclosed <think> prompts from the output text."""
    if not text:
        return ""
    # Strip full <think>...</think> tags along with any leading/trailing whitespace
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Handle cases where <think> tag was not closed properly
    cleaned = re.sub(r'<think>.*', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()

def get_active_groq_model(client):
    try:
        models_resp = client.models.list()
        active_ids = [m.id for m in models_resp.data]
        
        # Priority list targeting non-reasoning chat completion models
        preferred = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "llama3-70b-8192"
        ]
        for p in preferred:
            if p in active_ids:
                return p
        
        excluded_keywords = ["whisper", "vision", "orpheus", "guard", "classify", "classifier", "moderation", "rerank", "embed", "oss", "reasoning", "r1", "qwq"]
        for m_id in active_ids:
            if not any(k in m_id.lower() for k in excluded_keywords):
                return m_id
    except Exception:
        pass
    return "llama-3.3-70b-versatile"

def build_strict_dataset_summary(df):
    """Computes a compact aggregated summary of historical logs to fit Groq payload limits."""
    if df.empty or "Family" not in df.columns or "Win multiplier" not in df.columns:
        return "No historical log data available."
    
    fam_stats = df.groupby("Family").agg(
        hits=("Win multiplier", "count"),
        avg_spins=("Spin of feature hit", "mean"),
        avg_mult=("Win multiplier", "mean"),
        max_mult=("Win multiplier", "max")
    ).reset_index()
    
    summary_lines = []
    for _, row in fam_stats.iterrows():
        summary_lines.append(
            f"• {row['Family']}: Hits={row['hits']}, AvgSpins={row['avg_spins']:.1f}, AvgMult={row['avg_mult']:.1f}x, MaxMult={row['max_mult']:.1f}x"
        )
    
    compact_summary = "\n".join(summary_lines)
    return f"HISTORICAL DATA SUMMARY ({len(df)} total hits):\n{compact_summary}"

tab1, tab2, tab3 = st.tabs(["📲 Live Feature Logger", "💬 Interactive AI Co-Pilot", "📊 Visual Analytics"])

try:
    df = load_data()
    global_data_summary = build_strict_dataset_summary(df)

    # --- TAB 1: MOBILE FEATURE LOGGER ---
    with tab1:
        st.subheader("📝 Quick Log Feature Hit")
        
        families = safe_unique_options(df.get("Family"), ["Dragon Link", "Dragon Cash", "Lightning Link"])
        slots = safe_unique_options(df.get("Slot"), ["Panda Magic", "Golden Century", "Happy & Prosperous"])
        feature_types = safe_unique_options(df.get("Feature type"), ["Hold & Spin", "Free Games", "Major Progressive"])

        families_opts = families + ["➕ Add New Family..."]
        slots_opts = slots + ["➕ Add New Slot..."]

        log_date = st.date_input("Select Date", date.today(), key="main_date_picker")
        auto_day = log_date.strftime("%A")
        formatted_date_str = f"{log_date.month}/{log_date.day}/{log_date.year}"
        
        st.markdown(f"**Auto-detected Day:** `{auto_day}` | **Formatted Date:** `{formatted_date_str}`")

        with st.form("mobile_logger_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            
            with col1:
                sel_family = st.selectbox("Family", families_opts)
                custom_family = st.text_input("Enter New Family Name", value="") if sel_family == "➕ Add New Family..." else ""
                
                sel_slot = st.selectbox("Slot", slots_opts)
                custom_slot = st.text_input("Enter New Slot Name", value="") if sel_slot == "➕ Add New Slot..." else ""
                
                log_feature = st.selectbox("Feature Type", feature_types)
                
            with col2:
                log_spin = st.number_input("Spin of Feature Hit", min_value=1, value=50, step=1)
                log_win = st.number_input("Win Amount ($)", min_value=0.0, value=125.0, step=5.0)
                log_mult = st.number_input("Win Multiplier (x)", min_value=0.0, value=50.0, step=1.0)
                log_hit_num = st.number_input("Hit Number", min_value=1, value=1, step=1)
                log_attempt = st.number_input("Attempt Number", min_value=1, value=1, step=1)

            submitted = st.form_submit_button("🔥 Log Hit to Google Sheet", use_container_width=True)

            if submitted:
                try:
                    final_family = custom_family.strip() if sel_family == "➕ Add New Family..." else sel_family
                    final_slot = custom_slot.strip() if sel_slot == "➕ Add New Slot..." else sel_slot
                    
                    if not final_family or not final_slot:
                        st.error("Please specify a valid Family and Slot name.")
                    else:
                        ws = get_worksheet()
                        new_row = [
                            formatted_date_str,
                            auto_day,
                            final_family,
                            final_slot,
                            log_spin,
                            log_feature,
                            log_win,
                            log_mult,
                            log_hit_num,
                            log_attempt
                        ]
                        ws.append_row(new_row)
                        st.success(f"✅ Recorded: {final_family} ({final_slot}) hit on {auto_day} ({formatted_date_str})!")
                        st.cache_data.clear()
                except Exception as ex:
                    st.error(f"Failed to save record: {ex}")

    # --- TAB 2: INTERACTIVE CHAT & CO-PILOT ---
    with tab2:
        st.subheader("💬 Slotpilot AI Assistant")
        
        mode = st.radio("Select Assistance Mode:", ["🎮 Pre-Game Machine Finder", "⚡ Live Mid-Game Co-Pilot"], horizontal=True)
        st.divider()

        if mode == "🎮 Pre-Game Machine Finder":
            st.markdown("### 🎯 Pre-Session Selection Engine")
            st.caption("Ask Slotpilot which machine to play first based on your daily bankroll, profit target, and logged historical performance.")
            
            c_p1, c_p2, c_p3 = st.columns(3)
            with c_p1:
                total_bankroll = st.number_input("Total Day Bankroll ($)", value=1000.0, step=100.0, key="pre_bankroll")
            with c_p2:
                target_profit = st.number_input("Target Exit Goal ($)", value=1750.0, step=50.0, key="pre_target")
            with c_p3:
                risk_pref = st.selectbox("Risk Preference", ["Balanced", "High Volatility (Big Multipliers)", "Conservative (Short Spin Cycles)"], key="pre_risk")

            system_instruction_pre = f"""
            You are Slotpilot. Provide direct, short, bulleted answers. NO intro fluff, NO explanations, NO internal thinking text.
            {global_data_summary}

            User Context: Bankroll: ${total_bankroll} | Target Exit: ${target_profit} | Profile: {risk_pref}

            RESPONSE FORMAT (STRICT):
            - Recommend top 2 families from dataset.
            - For each, provide ONLY:
              * Family Name
              * Check-In ($)
              * Bet Size ($)
              * Max Runway (Spins = Check-In / Bet Size)
            - Keep total response under 60 words.
            """

            if "pre_messages" not in st.session_state:
                st.session_state.pre_messages = [
                    {"role": "assistant", "content": f"👋 **Ready.** Starting Bankroll: **${total_bankroll}** | Exit Target: **${target_profit}**.\nAsk me which machine to play first!"}
                ]

            for msg in st.session_state.pre_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if user_pre_input := st.chat_input("e.g. 'Recommend ideal slot choice, starting bet, and check-in amount for tonight.'"):
                st.session_state.pre_messages.append({"role": "user", "content": user_pre_input})
                with st.chat_message("user"):
                    st.markdown(user_pre_input)

                if client:
                    with st.chat_message("assistant"):
                        active_model = get_active_groq_model(client)
                        recent = st.session_state.pre_messages[-4:]
                        msgs = [{"role": "system", "content": system_instruction_pre}] + [{"role": m["role"], "content": m["content"]} for m in recent]
                        try:
                            res = client.chat.completions.create(
                                model=active_model,
                                messages=msgs,
                                temperature=0.1,
                                max_tokens=200
                            )
                            raw_reply = res.choices[0].message.content or ""
                            reply = clean_thinking_tags(raw_reply)
                            st.markdown(reply)
                            st.session_state.pre_messages.append({"role": "assistant", "content": reply})
                        except Exception as err:
                            st.error(f"Groq API Error: {err}")

        else:
            # Live Mid-Game Co-Pilot
            unique_families = safe_unique_options(df.get("Family"), ["Dragon Link"])
            
            with st.expander("⚙️ Set Live Machine & Check-In Context", expanded=True):
                ca, cb, cc, cd = st.columns(4)
                with ca:
                    selected_family = st.selectbox("Target Slot Family", options=unique_families, key="chat_family")
                
                filtered_df_fam = df[df["Family"].astype(str) == str(selected_family)] if "Family" in df else df
                available_slots = safe_unique_options(filtered_df_fam.get("Slot"), ["Panda Magic", "All Slots"])
                slot_options = ["All Slots"] + [s for s in available_slots if s != "All Slots"]
                
                with cb:
                    selected_slot = st.selectbox("Target Slot Machine", options=slot_options, key="chat_slot")
                with cc:
                    checkin_amount = st.number_input("Machine Check-In Amount ($)", value=500.0, step=50.0, key="chat_checkin")
                with cd:
                    current_bet = st.number_input("Base Bet ($)", value=2.50, step=0.50, key="chat_bet")

            if selected_slot != "All Slots" and "Slot" in df:
                machine_df = df[(df["Family"].astype(str) == str(selected_family)) & (df["Slot"].astype(str) == str(selected_slot))]
            else:
                machine_df = filtered_df_fam

            m_hits = len(machine_df)
            m_avg_spins = round(machine_df["Spin of feature hit"].mean(), 1) if ("Spin of feature hit" in machine_df and not machine_df.empty) else 0
            m_avg_mult = round(machine_df["Win multiplier"].mean(), 1) if ("Win multiplier" in machine_df and not machine_df.empty) else 0
            
            max_spins_runway = int(checkin_amount / current_bet) if current_bet > 0 else 0

            system_instruction_live = f"""
            You are Slotpilot. Give direct, short, tactical play advice. NO fluff. NO meta commentary.
            {global_data_summary}

            Live Context:
            - Family: "{selected_family}" | Slot: "{selected_slot}"
            - Check-In: ${checkin_amount} | Bet: ${current_bet} | Max Runway: {max_spins_runway} spins
            - Stats: Avg Spins: {m_avg_spins}, Avg Mult: {m_avg_mult}x

            STRICT FORMAT: Max 2 bullet points, under 40 words total.
            """

            if "messages" not in st.session_state:
                st.session_state.messages = [
                    {
                        "role": "assistant",
                        "content": f"🎯 **Ready for {selected_family} ({selected_slot}) session.**\n- Check-In: ${checkin_amount}\n- Target cycle: ~{m_avg_spins} spins.\n- Max runway: {max_spins_runway} spins at ${current_bet}."
                    }
                ]

            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if user_input := st.chat_input("e.g. '35 spins in, balance down to 378, bet set at $3.75. What next?'"):
                st.session_state.messages.append({"role": "user", "content": user_input})
                with st.chat_message("user"):
                    st.markdown(user_input)

                if client:
                    with st.chat_message("assistant"):
                        active_model = get_active_groq_model(client)
                        recent_messages = st.session_state.messages[-4:]
                        groq_messages = [{"role": "system", "content": system_instruction_live}] + [
                            {"role": m["role"], "content": m["content"]} for m in recent_messages
                        ]
                        
                        try:
                            res = client.chat.completions.create(
                                model=active_model,
                                messages=groq_messages,
                                temperature=0.1,
                                max_tokens=150
                            )
                            raw_reply = res.choices[0].message.content or ""
                            reply = clean_thinking_tags(raw_reply)
                            st.markdown(reply)
                            st.session_state.messages.append({"role": "assistant", "content": reply})
                        except Exception as err:
                            st.error(f"Groq API Error: {err}")

        if st.button("🔄 Reset Chat Session"):
            st.session_state.messages = []
            st.session_state.pre_messages = []
            st.rerun()

    # --- TAB 3: VISUAL ANALYTICS ---
    with tab3:
        st.subheader("📊 Deep Session Visualizers")
        
        with st.expander("🎛️ Unified Master Filters", expanded=True):
            f_col1, f_col2, f_col3 = st.columns(3)
            
            all_families = safe_unique_options(df.get("Family"), [])
            all_slots = safe_unique_options(df.get("Slot"), [])
            
            with f_col1:
                selected_fam_filter = st.multiselect("Filter by Family", options=all_families, default=[], key="master_fam_filter")
            with f_col2:
                if selected_fam_filter:
                    available_slots_filtered = safe_unique_options(df[df["Family"].isin(selected_fam_filter)].get("Slot"), [])
                else:
                    available_slots_filtered = all_slots
                selected_slot_filter = st.multiselect("Filter by Slot Title", options=available_slots_filtered, default=[], key="master_slot_filter")
            with f_col3:
                min_spins, max_spins = 1, int(df["Spin of feature hit"].max()) if ("Spin of feature hit" in df and not df.empty) else 200
                spin_range = st.slider("Filter by Feature Spin Window", min_value=1, max_value=max_spins, value=(1, max_spins), key="master_spin_slider")

        filtered_df = df.copy()
        
        if selected_fam_filter:
            filtered_df = filtered_df[filtered_df["Family"].isin(selected_fam_filter)]
            
        if selected_slot_filter:
            filtered_df = filtered_df[filtered_df["Slot"].isin(selected_slot_filter)]
            
        if "Spin of feature hit" in filtered_df.columns:
            filtered_df = filtered_df[
                (filtered_df["Spin of feature hit"] >= spin_range[0]) & 
                (filtered_df["Spin of feature hit"] <= spin_range[1])
            ]

        st.caption(f"Showing **{len(filtered_df)}** of **{len(df)}** total logged feature hits.")
        st.divider()

        v_col1, v_col2 = st.columns(2)
        
        with v_col1:
            if not filtered_df.empty and "Spin of feature hit" in filtered_df and "Win multiplier" in filtered_df:
                fig_scatter = px.scatter(
                    filtered_df,
                    x="Spin of feature hit",
                    y="Win multiplier",
                    color="Family",
                    hover_data=["Slot"] if "Slot" in filtered_df else None,
                    title="1. Volatility Radar: Spin Depth vs Multiplier Yield",
                    labels={"Spin of feature hit": "Spins to Trigger Feature", "Win multiplier": "Win Multiplier (x)"}
                )
                st.plotly_chart(fig_scatter, use_container_width=True)
            else:
                st.info("No data available for Volatility Radar given current filters.")

        with v_col2:
            if not filtered_df.empty and "Day" in filtered_df and "Win multiplier" in filtered_df:
                heatmap_data = filtered_df.groupby(["Day", "Family"])["Win multiplier"].mean().reset_index()
                fig_heat = px.density_heatmap(
                    heatmap_data,
                    x="Day",
                    y="Family",
                    z="Win multiplier",
                    title="2. Day-of-Week Multiplier Yield Heatmap",
                    color_continuous_scale="Viridis"
                )
                st.plotly_chart(fig_heat, use_container_width=True)
            else:
                st.info("No data available for Yield Heatmap given current filters.")

        v_col3, v_col4 = st.columns(2)
        
        with v_col3:
            if not filtered_df.empty and "Spin of feature hit" in filtered_df:
                fig_hist = px.histogram(
                    filtered_df, 
                    x="Spin of feature hit", 
                    nbins=25, 
                    cumulative=True,
                    title="3. Cumulative Feature Trigger Probability (CDF)",
                    color_discrete_sequence=["#10B981"],
                    labels={"Spin of feature hit": "Spins Elapsed"}
                )
                st.plotly_chart(fig_hist, use_container_width=True)
            else:
                st.info("No data available for Cumulative Trigger Probability given current filters.")

        with v_col4:
            if not filtered_df.empty and "Family" in filtered_df and "Win multiplier" in filtered_df:
                fig_box = px.box(
                    filtered_df, 
                    x="Family", 
                    y="Win multiplier", 
                    title="4. Win Multiplier Distribution by Family",
                    color="Family"
                )
                st.plotly_chart(fig_box, use_container_width=True)
            else:
                st.info("No data available for Multiplier Distribution given current filters.")

except Exception as e:
    st.error(f"Error loading app: {e}")
