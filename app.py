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
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'<think>.*', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()

def get_active_groq_model(client):
    try:
        models_resp = client.models.list()
        active_ids = [m.id for m in models_resp.data]
        
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

def determine_ai_bet_strategy(spins_series, mult_series):
    """
    AI Logic Engine: Dynamically determines betting structure (Flat High, Flat Low, Variable)
    based on distribution variance, modal clusters, and multiplier stability.
    """
    if spins_series.empty:
        return "Flat Mid-Bet", "$5.00", "10c Denom / $5 Bet", 5.00
    
    median_spins = spins_series.median()
    std_spins = spins_series.std() if len(spins_series) > 1 else 0
    avg_mult = mult_series.mean() if not mult_series.empty else 0
    max_mult = mult_series.max() if not mult_series.empty else 0
    
    # High Consistency + Early Hit Density = Flat High Play
    if median_spins <= 30 and (std_spins <= 12 or len(spins_series) <= 2):
        if avg_mult >= 40 or max_mult >= 100:
            return "Flat High-Bet", "$10.00", "$1 Denom / $10 Bet", 10.00
        else:
            return "Flat High-Bet", "$10.00", "10c Denom / $10 Bet", 10.00
            
    # High Multiplier Variance + Broader Spin Distribution = Variable Progression
    elif std_spins > 15 and max_mult >= 80:
        return "Varying (Scale Up)", "$2.50 ➔ $7.50", "10c Denom ($2.50 to $7.50)", 7.50
        
    # Deep Spin Cycles + Low Multiplier Cap = Flat Low Safety
    elif median_spins > 45 and avg_mult < 35:
        return "Flat Low-Bet", "$2.50", "1c or 2c Denom / $2.50 Bet", 2.50
        
    # Standard Volatility Machine = Mid/High Balanced Bet
    else:
        if avg_mult >= 50:
            return "Flat Mid-High", "$7.50", "10c Denom / $7.50 Bet", 7.50
        else:
            return "Flat Mid-Bet", "$5.00", "5c Denom / $5.00 Bet", 5.00

def build_strict_dataset_summary(df, target_family=None, max_rows=15):
    """Computes a compact aggregated summary including Median and Density Zones to prevent outlier distortion."""
    if df.empty or "Family" not in df.columns or "Win multiplier" not in df.columns:
        return "No historical log data available."
    
    temp_df = df.copy()
    if target_family and target_family != "All Families":
        temp_df = temp_df[temp_df["Family"].astype(str) == str(target_family)]
        
    group_cols = ["Family", "Slot"] if "Slot" in temp_df.columns else ["Family"]
    
    summary_lines = []
    
    for name, group in temp_df.groupby(group_cols):
        slot_name = name[1] if isinstance(name, tuple) else 'General'
        fam_name = name[0] if isinstance(name, tuple) else name
        
        hits = len(group)
        mean_spins = group["Spin of feature hit"].mean()
        median_spins = group["Spin of feature hit"].median()
        avg_mult = group["Win multiplier"].mean()
        max_mult = group["Win multiplier"].max()
        
        spins_list = group["Spin of feature hit"].dropna().tolist()
        density_str = "N/A"
        if spins_list:
            buckets = [int(s // 10 * 10) for s in spins_list]
            if buckets:
                top_bucket = max(set(buckets), key=buckets.count)
                density_str = f"{top_bucket}-{top_bucket+10} spins"
        
        summary_lines.append(
            f"• Fam: '{fam_name}' | Slot: '{slot_name}' | Hits={hits}, MeanSpins={int(round(mean_spins))}, MedianSpins={int(round(median_spins))}, PeakWinZone='{density_str}', AvgMult={avg_mult:.1f}x, MaxMult={max_mult:.1f}x"
        )
        if len(summary_lines) >= max_rows:
            break
            
    compact_summary = "\n".join(summary_lines)
    return f"HISTORICAL LOG SUMMARY (CLUSTER-AWARE): \n{compact_summary}"

DOMAIN_KNOWLEDGE_PROMPT = """
COLUMN MEANINGS & STRATEGY CONTEXT:
1. Attempt Number & Hit Number Logic:
   - Attempt Number = 1 & Hit Number = 1: Machine hit a feature on the initial check-in.
   - Attempt Number = 1 & Hit Number = 0: Initial check-in failed to hit a feature.
   - Attempt Number = 2, 3, 4+: Player continued playing after a feature win (Repeat Attempt).

2. Outlier vs. High-Density Winning Zone Principle (CRITICAL):
   - Ignore simple arithmetic averages if 1-2 extreme cold games (e.g., 115-120 spins) skew the overall mean upward.
   - Focus on the MODAL DENSITY RANGE (the specific spin bracket where the majority of feature hits occur).

3. Autonomous AI Betting Strategy Selection:
   - The AI agent dynamically selects the bet strategy (Flat High, Flat Low, Flat Mid, or Varying Sizing) per slot.
   - Do NOT force a single bet rule across all slots. Evaluate volatility, cluster tightness, and yield profiles.

4. Softened Runway Rule (10% to 15% Buffer on Cluster Target):
   - Take the high-density cluster target (or median) and add a 10% to 15% softening buffer. Round to integers.
"""

tab1, tab2, tab3, tab4 = st.tabs([
    "📲 Live Feature Logger", 
    "💬 Interactive AI Co-Pilot", 
    "📊 Visual Analytics", 
    "🎯 Today's Priority Board"
])

try:
    df = load_data()

    # --- TAB 1: MOBILE FEATURE LOGGER ---
    with tab1:
        st.subheader("📝 Quick Log Feature Hit")
        
        families = safe_unique_options(df.get("Family"), ["Dragon Link", "Dragon Cash", "Lightning Link"])
        slots = safe_unique_options(df.get("Slot"), ["Panda Magic", "Golden Century", "Happy & Prosperous", "N/A"])
        feature_types = safe_unique_options(df.get("Feature type"), ["Hold & Spin", "Free Games", "Major Progressive", "N/A"])

        families_opts = families + ["➕ Add Custom / N/A..."]
        slots_opts = slots + ["➕ Add Custom / N/A..."]
        feature_opts = feature_types + ["➕ Add Custom / N/A..."]

        log_date = st.date_input("Select Date", date.today(), key="main_date_picker")
        auto_day = log_date.strftime("%A")
        formatted_date_str = f"{log_date.month}/{log_date.day}/{log_date.year}"
        
        st.markdown(f"**Auto-detected Day:** `{auto_day}` | **Formatted Date:** `{formatted_date_str}`")

        with st.form("mobile_logger_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            
            with col1:
                sel_family = st.selectbox("Family", families_opts)
                custom_family = st.text_input("Enter Family Name (or N/A)", value="") if sel_family == "➕ Add Custom / N/A..." else ""
                
                sel_slot = st.selectbox("Slot", slots_opts)
                custom_slot = st.text_input("Enter Slot Name (or N/A)", value="") if sel_slot == "➕ Add Custom / N/A..." else ""
                
                sel_feature = st.selectbox("Feature Type", feature_opts)
                custom_feature = st.text_input("Enter Feature Type (or N/A)", value="") if sel_feature == "➕ Add Custom / N/A..." else ""
                
            with col2:
                log_spin = st.number_input("Spin of Feature Hit", min_value=1, value=50, step=1)
                log_win = st.number_input("Win Amount ($)", min_value=0.0, value=125.0, step=5.0)
                log_mult = st.number_input("Win Multiplier (x)", min_value=0.0, value=50.0, step=1.0)
                log_hit_num = st.number_input("Hit Number (0 = Failed attempt, 1+ = Hit #)", min_value=0, value=1, step=1)
                log_attempt = st.number_input("Attempt Number (1 = Initial Check-In, 2+ = Repeat Attempt)", min_value=1, value=1, step=1)

            submitted = st.form_submit_button("🔥 Log Hit to Google Sheet", use_container_width=True)

            if submitted:
                try:
                    final_family = custom_family.strip() if sel_family == "➕ Add Custom / N/A..." else sel_family
                    final_slot = custom_slot.strip() if sel_slot == "➕ Add Custom / N/A..." else sel_slot
                    final_feature = custom_feature.strip() if sel_feature == "➕ Add Custom / N/A..." else sel_feature
                    
                    if not final_family or not final_slot or not final_feature:
                        st.error("Please fill in all field selections properly.")
                    else:
                        ws = get_worksheet()
                        new_row = [
                            formatted_date_str,
                            auto_day,
                            final_family,
                            final_slot,
                            log_spin,
                            final_feature,
                            log_win,
                            log_mult,
                            log_hit_num,
                            log_attempt
                        ]
                        ws.append_row(new_row, value_input_option="USER_ENTERED")
                        st.success(f"✅ Recorded: {final_family} ({final_slot}) hit on {auto_day} ({formatted_date_str})!")
                        st.cache_data.clear()
                except Exception as ex:
                    st.error(f"Failed to save record: {ex}")

        if not df.empty:
            st.divider()
            csv_data = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Backup Data: Download Session History (CSV)",
                data=csv_data,
                file_name=f"slot_session_log_backup_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True
            )

    # --- TAB 2: INTERACTIVE CHAT & CO-PILOT ---
    with tab2:
        st.subheader("💬 Slotpilot AI Assistant")
        
        mode = st.radio("Select Assistance Mode:", ["🎮 Pre-Game Machine Finder", "⚡ Live Mid-Game Co-Pilot"], horizontal=True)
        st.divider()

        if mode == "🎮 Pre-Game Machine Finder":
            st.markdown("### 🎯 Autonomous Pre-Session Engine")
            st.caption("Let the AI analyze historical variance to determine whether to play Flat High, Flat Low, or Varying bets.")
            
            c_p1, c_p2, c_p3 = st.columns(3)
            with c_p1:
                total_bankroll = st.number_input("Total Day Bankroll ($)", value=2500.0, step=250.0, key="pre_bankroll")
            with c_p2:
                target_profit = st.number_input("Target Exit Goal ($)", value=4000.0, step=250.0, key="pre_target")
            with c_p3:
                risk_pref = st.selectbox("Risk Preference", ["AI Dynamic Decision", "Aggressive High-Roller", "Conservative Capital Safety"], key="pre_risk")

            pre_summary = build_strict_dataset_summary(df, max_rows=10)

            system_instruction_pre = f"""
            You are Slotpilot, an expert slot strategy co-pilot. Provide direct, bulleted answers. NO intro fluff, NO internal thinking text.

            {DOMAIN_KNOWLEDGE_PROMPT}

            {pre_summary}

            User Context: Bankroll: ${total_bankroll} | Target Exit: ${target_profit} | Profile: {risk_pref}

            RESPONSE FORMAT (STRICT):
            - Recommend top 2 specific slot recommendations.
            - Include both Family Name AND exact Slot Name.
            - Provide ONLY:
              * Family & Specific Slot Name
              * AI Bet Strategy (Flat High, Flat Low, Flat Mid, or Varying Bet)
              * Recommended Denomination & Bet Config
              * Recommended Check-In ($)
              * Softened Density Target (Spins)
            - Keep total response concise (under 90 words).
            """

            if "pre_messages" not in st.session_state:
                st.session_state.pre_messages = [
                    {"role": "assistant", "content": f"👋 **Ready.** Bankroll: **${total_bankroll}** | Exit Target: **${target_profit}**.\nAsk me to evaluate machine bet strategies for today!"}
                ]

            for msg in st.session_state.pre_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if user_pre_input := st.chat_input("e.g. 'Which machine should I play first and should I use flat or varying bet sizing?'"):
                st.session_state.pre_messages.append({"role": "user", "content": user_pre_input})
                with st.chat_message("user"):
                    st.markdown(user_pre_input)

                if client:
                    with st.chat_message("assistant"):
                        active_model = get_active_groq_model(client)
                        recent = st.session_state.pre_messages[-2:]
                        msgs = [{"role": "system", "content": system_instruction_pre}] + [{"role": m["role"], "content": m["content"]} for m in recent]
                        try:
                            res = client.chat.completions.create(
                                model=active_model,
                                messages=msgs,
                                temperature=0.1,
                                max_tokens=250
                            )
                            raw_reply = res.choices[0].message.content or ""
                            reply = clean_thinking_tags(raw_reply)
                            st.markdown(reply)
                            st.session_state.pre_messages.append({"role": "assistant", "content": reply})
                        except Exception as err:
                            st.error(f"Groq API Error: {err}")

            st.divider()
            st.markdown("#### ⚡ Pass Selection to Live Co-Pilot & Analytics")
            
            fam_options = safe_unique_options(df.get("Family"), ["Dragon Link"])
            slot_options_all = safe_unique_options(df.get("Slot"), ["Panda Magic"])
            
            sc1, sc2, sc3, sc4 = st.columns(4)
            with sc1:
                sel_fam_to_pass = st.selectbox("Chosen Family", fam_options, key="pass_fam")
            with sc2:
                sel_slot_to_pass = st.selectbox("Chosen Slot", slot_options_all, key="pass_slot")
            with sc3:
                sel_checkin_to_pass = st.number_input("Check-In ($)", value=750.0, step=50.0, key="pass_checkin")
            with sc4:
                sel_bet_to_pass = st.number_input("Bet Size ($)", value=10.00, step=2.50, key="pass_bet")
                
            if st.button("🚀 Launch Live Session with Selection", use_container_width=True):
                st.session_state["live_family"] = sel_fam_to_pass
                st.session_state["live_slot"] = sel_slot_to_pass
                st.session_state["live_checkin"] = sel_checkin_to_pass
                st.session_state["live_bet"] = sel_bet_to_pass
                st.session_state["master_fam_filter"] = [sel_fam_to_pass]
                st.session_state["master_slot_filter"] = [sel_slot_to_pass] if sel_slot_to_pass != "All Slots" else []
                st.session_state.messages = []
                st.success(f"Configured Live Co-Pilot & Visual Analytics for **{sel_fam_to_pass} - {sel_slot_to_pass}**!")

        else:
            # Live Mid-Game Co-Pilot
            unique_families = safe_unique_options(df.get("Family"), ["Dragon Link"])
            
            default_fam = st.session_state.get("live_family", unique_families[0] if unique_families else "Dragon Link")
            default_checkin = st.session_state.get("live_checkin", 750.0)
            default_bet = st.session_state.get("live_bet", 10.00)
            
            with st.expander("⚙️ Set Live Machine & Check-In Context", expanded=True):
                ca, cb, cc, cd = st.columns(4)
                with ca:
                    fam_index = unique_families.index(default_fam) if default_fam in unique_families else 0
                    selected_family = st.selectbox("Target Slot Family", options=unique_families, index=fam_index, key="chat_family")
                
                filtered_df_fam = df[df["Family"].astype(str) == str(selected_family)] if "Family" in df else df
                available_slots = safe_unique_options(filtered_df_fam.get("Slot"), ["Panda Magic", "All Slots"])
                slot_options = ["All Slots"] + [s for s in available_slots if s != "All Slots"]
                
                default_slot = st.session_state.get("live_slot", "All Slots")
                slot_index = slot_options.index(default_slot) if default_slot in slot_options else 0
                
                with cb:
                    selected_slot = st.selectbox("Target Slot Machine", options=slot_options, index=slot_index, key="chat_slot")
                with cc:
                    checkin_amount = st.number_input("Machine Check-In Amount ($)", value=default_checkin, step=50.0, key="chat_checkin")
                with cd:
                    current_bet = st.number_input("Base Bet ($)", value=default_bet, step=2.50, key="chat_bet")

            if selected_slot != "All Slots" and "Slot" in df:
                machine_df = df[(df["Family"].astype(str) == str(selected_family)) & (df["Slot"].astype(str) == str(selected_slot))]
            else:
                machine_df = filtered_df_fam

            m_median_spins_val = machine_df["Spin of feature hit"].median() if ("Spin of feature hit" in machine_df and not machine_df.empty) else 0
            m_median_spins = int(round(m_median_spins_val)) if pd.notnull(m_median_spins_val) else 0
            
            m_softened_spins = int(round(m_median_spins * 1.12)) if m_median_spins > 0 else 0
            m_avg_mult = round(machine_df["Win multiplier"].mean(), 1) if ("Win multiplier" in machine_df and not machine_df.empty) else 0
            
            max_spins_runway = int(checkin_amount / current_bet) if current_bet > 0 else 0

            live_dataset_summary = build_strict_dataset_summary(df, target_family=selected_family, max_rows=5)

            system_instruction_live = f"""
            You are Slotpilot, a live tactical slot assistant. Give direct, tactical advice. NO fluff.

            {DOMAIN_KNOWLEDGE_PROMPT}

            {live_dataset_summary}

            Live Context:
            - Family: "{selected_family}" | Slot: "{selected_slot}"
            - Check-In: ${checkin_amount} | Bet: ${current_bet} | Max Runway: {max_spins_runway} spins
            - Median Spin Baseline (ignoring outlier spikes): {m_median_spins} spins
            - Softened Target Window (Cluster Baseline + 12% buffer): {m_softened_spins} spins
            - Avg Multiplier: {m_avg_mult}x

            STRICT FORMAT: Max 2 bullet points, under 50 words total.
            """

            if "messages" not in st.session_state or not st.session_state.messages:
                st.session_state.messages = [
                    {
                        "role": "assistant",
                        "content": f"🎯 **Ready for {selected_family} ({selected_slot}) session.**\n- Check-In: ${checkin_amount}\n- High-Density Softened Window: ~{m_softened_spins} spins (filtered for outliers + 12% safety buffer).\n- Max Runway: {max_spins_runway} spins at ${current_bet}."
                    }
                ]

            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if user_input := st.chat_input("e.g. 'Attempt 1 failed at 28 spins. Should I start Attempt 2 repeat play?'"):
                st.session_state.messages.append({"role": "user", "content": user_input})
                with st.chat_message("user"):
                    st.markdown(user_input)

                if client:
                    with st.chat_message("assistant"):
                        active_model = get_active_groq_model(client)
                        recent_messages = st.session_state.messages[-2:]
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
                selected_fam_filter = st.multiselect("Filter by Family", options=all_families, key="master_fam_filter")
            with f_col2:
                if selected_fam_filter:
                    available_slots_filtered = safe_unique_options(df[df["Family"].isin(selected_fam_filter)].get("Slot"), [])
                else:
                    available_slots_filtered = all_slots
                selected_slot_filter = st.multiselect("Filter by Slot Title", options=available_slots_filtered, key="master_slot_filter")
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

    # --- TAB 4: TODAY'S DAILY PRIORITY BOARD (AUTONOMOUS AI BET STRATEGY) ---
    with tab4:
        st.subheader("🎯 Today's Pre-Calculated Priority Board (Autonomous AI Betting Sizing)")
        
        today_date = date.today()
        today_day_str = today_date.strftime("%A")
        today_formatted = f"{today_date.month}/{today_date.day}/{today_date.year}"
        
        st.info(f"📅 **Date Detected:** `{today_formatted}` (`{today_day_str}`)\nEvaluating slot variance profiles for {today_day_str}s. AI dynamically decides whether each machine requires Flat High, Flat Low, or Varying Bet structures.")
        
        if df.empty:
            st.warning("No dataset loaded. Please check Google Sheets connection.")
        else:
            day_filtered_df = df[df["Day"].astype(str).str.strip().str.lower() == today_day_str.lower()] if "Day" in df else pd.DataFrame()
            analysis_df = day_filtered_df if len(day_filtered_df) >= 5 else df
            
            group_cols = ["Family", "Slot"] if "Slot" in analysis_df.columns else ["Family"]
            priority_records = []
            
            for name, group in analysis_df.groupby(group_cols):
                fam = name[0] if isinstance(name, tuple) else name
                s_name = name[1] if isinstance(name, tuple) else "General"
                
                total_hits = len(group)
                if total_hits == 0:
                    continue
                    
                spins = group["Spin of feature hit"].dropna()
                mults = group["Win multiplier"].dropna() if "Win multiplier" in group else pd.Series()
                
                if spins.empty:
                    continue
                
                # Outlier-resistant metrics
                median_spins = spins.median()
                buckets = [int(s // 10 * 10) for s in spins]
                top_bucket = max(set(buckets), key=buckets.count) if buckets else int(median_spins)
                modal_mid = top_bucket + 5
                
                raw_target_spins = (modal_mid * 0.6) + (median_spins * 0.4)
                softened_cycle_spins = int(round(raw_target_spins * 1.12))
                if softened_cycle_spins < 15:
                    softened_cycle_spins = 15
                
                avg_mult = mults.mean() if not mults.empty else 0
                max_mult = mults.max() if not mults.empty else 0
                
                # Autonomous AI Engine evaluates volatility pattern
                bet_strategy, bet_display, denom_config, base_bet_num = determine_ai_bet_strategy(spins, mults)
                
                # Dynamic Check-In calculation based on assigned strategy bet level
                raw_checkin = softened_cycle_spins * base_bet_num * 1.8
                suggested_checkin = max(300.0, float(((int(raw_checkin) + 49) // 50) * 50))
                
                # Composite Score based on overall machine efficiency
                score = (avg_mult * 0.4) + (max_mult * 0.2) + (total_hits * 2.5) + (base_bet_num * 2.0) - (softened_cycle_spins * 0.15)
                
                priority_records.append({
                    "Family": fam,
                    "Slot Title": s_name,
                    "AI Strategy": bet_strategy,
                    "Recommended Bet": bet_display,
                    "Setup Config": denom_config,
                    "Target Cycle (Spins)": softened_cycle_spins,
                    "Initial Check-In ($)": f"${suggested_checkin:.0f}",
                    "Peak Yield Zone": f"{top_bucket}-{top_bucket+10} spins",
                    "Avg Mult": f"{avg_mult:.1f}x",
                    "Hits": total_hits,
                    "Composite_Score": score,
                    "raw_bet": base_bet_num,
                    "raw_checkin": suggested_checkin
                })
            
            p_df = pd.DataFrame(priority_records)
            
            if not p_df.empty:
                p_df = p_df.sort_values(by="Composite_Score", ascending=False).reset_index(drop=True)
                p_df.index = p_df.index + 1
                
                top_15_df = p_df.head(15).drop(columns=["Composite_Score", "raw_bet", "raw_checkin"])
                
                st.markdown("### 🏆 Top 15 Recommended Machines (AI Strategy Evaluated)")
                st.caption("The AI agent evaluates historical density clusters and variance profiles to decide between Flat High, Flat Low, or Varying bets for each machine.")
                
                st.dataframe(
                    top_15_df,
                    use_container_width=True,
                    height=560
                )
                
                st.divider()
                st.markdown("#### 🚀 Quick Action: Load Top Priority Choice into Live Co-Pilot")
                top_row = p_df.iloc[0]
                
                st.success(f"**Top Choice:** {top_row['Family']} - {top_row['Slot Title']} | Strategy: **{top_row['AI Strategy']}** ({top_row['Recommended Bet']}) on **{top_row['Setup Config']}** for **{top_row['Target Cycle (Spins)']} spins**.")
                
                if st.button("⚡ Activate Top Priority Machine in Live Co-Pilot", use_container_width=True):
                    st.session_state["live_family"] = top_row['Family']
                    st.session_state["live_slot"] = top_row['Slot Title']
                    st.session_state["live_checkin"] = float(top_row['raw_checkin'])
                    st.session_state["live_bet"] = float(top_row['raw_bet'])
                    st.session_state["master_fam_filter"] = [top_row['Family']]
                    st.session_state["master_slot_filter"] = [top_row['Slot Title']]
                    st.session_state.messages = []
                    st.success("Configured! Switch to Tab 2 to begin mid-game tracking.")
            else:
                st.warning("Insufficient data available to pre-calculate priority ranks.")

except Exception as e:
    st.error(f"Error loading app: {e}")
