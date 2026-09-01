import os
import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from datetime import date
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

st.set_page_config(page_title="Slotpilot AI & Logger", layout="wide", initial_sidebar_state="collapsed")

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
            
    return df

def safe_unique_options(series, default_list):
    if series is not None and not series.empty:
        vals = [str(x) for x in series.unique() if x != "" and x is not None]
        if vals:
            return sorted(vals)
    return default_list

def get_active_groq_model(client):
    try:
        models_resp = client.models.list()
        active_ids = [m.id for m in models_resp.data]
        preferred = [
            "llama-3.3-70b-versatile",
            "llama-3.3-70b-specdec",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768"
        ]
        for p in preferred:
            if p in active_ids:
                return p
        for m_id in active_ids:
            if "whisper" not in m_id.lower() and "vision" not in m_id.lower() and "orpheus" not in m_id.lower():
                return m_id
    except Exception:
        pass
    return "llama3-70b-8192"

tab1, tab2, tab3 = st.tabs(["📲 Live Feature Logger", "💬 Interactive AI Co-Pilot", "📊 Visual Analytics"])

try:
    df = load_data()

    # --- TAB 1: MOBILE FEATURE LOGGER ---
    with tab1:
        st.subheader("📝 Quick Log Feature Hit")
        
        families = safe_unique_options(df.get("Family"), ["Dragon Link", "Dragon Cash", "Lightning Link"])
        slots = safe_unique_options(df.get("Slot"), ["Panda Magic", "Golden Century", "Happy & Prosperous"])
        feature_types = safe_unique_options(df.get("Feature type"), ["Hold & Spin", "Free Games", "Major Progressive"])

        families_opts = families + ["➕ Add New Family..."]
        slots_opts = slots + ["➕ Add New Slot..."]

        # Date Picker outside form triggers instant update
        log_date = st.date_input("Select Date", date.today(), key="main_date_picker")
        auto_day = log_date.strftime("%A")
        st.markdown(f"**Auto-detected Day:** `{auto_day}`")

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
                            log_date.strftime("%Y-%m-%d"),
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
                        st.success(f"✅ Recorded: {final_family} ({final_slot}) hit on {auto_day}!")
                        st.cache_data.clear()
                except Exception as ex:
                    st.error(f"Failed to save record: {ex}")

    # --- TAB 2: INTERACTIVE CHAT & MID-GAME CO-PILOT ---
    with tab2:
        st.subheader("💬 Live Session AI Co-Pilot")
        
        unique_families = safe_unique_options(df.get("Family"), ["Dragon Link"])
        
        # Session Setup Context Controls
        with st.expander("⚙️ Set Current Machine & Bankroll Context", expanded=True):
            ca, cb, cc = st.columns(3)
            with ca:
                selected_family = st.selectbox("Target Slot Family", options=unique_families, key="chat_family")
            with cb:
                bankroll = st.number_input("Starting Bankroll ($)", value=500, step=50, key="chat_bankroll")
            with cc:
                current_bet = st.number_input("Base Bet ($)", value=2.50, step=0.50, key="chat_bet")

        family_df = df[df["Family"].astype(str) == str(selected_family)] if "Family" in df else df
        fam_hits = len(family_df)
        fam_avg_spins = round(family_df["Spin of feature hit"].mean(), 1) if "Spin of feature hit" in family_df else 0
        fam_avg_mult = round(family_df["Win multiplier"].mean(), 1) if "Win multiplier" in family_df else 0
        
        system_instruction = f"""
        You are Slotpilot, a real-time mathematical slot co-pilot.
        Current Context:
        - Family: {selected_family} (Avg Spins to Feature: {fam_avg_spins}, Avg Multiplier: {fam_avg_mult}x, Sample Size: {fam_hits} hits)
        - Session Bankroll: ${bankroll}
        - Base Bet: ${current_bet}

        CRITICAL RESPONSE RULES:
        1. Keep responses ultra-concise (maximum 3 bullet points, under 60 words total).
        2. Be direct, actionable, and rapid for mid-game live play.
        3. Never write generic filler or long intro paragraphs.
        """

        # Initialize Chat History
        if "messages" not in st.session_state:
            st.session_state.messages = [
                {
                    "role": "assistant",
                    "content": f"🎯 **Ready for {selected_family} session.**\n- Target cycle: ~{fam_avg_spins} spins.\n- Max runway: {int(bankroll / current_bet if current_bet else 0)} spins.\n- Update me on spin count or balance anytime!"
                }
            ]

        # Display Chat History
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Chat Input Bar for Mid-Game Updates
        if user_input := st.chat_input("e.g. '50 spins in, no feature, down $125. Should I continue?'"):
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.markdown(user_input)

            if not client:
                st.error("Groq API Key missing.")
            else:
                with st.chat_message("assistant"):
                    active_model = get_active_groq_model(client)
                    groq_messages = [{"role": "system", "content": system_instruction}] + [
                        {"role": m["role"], "content": m["content"]} for m in st.session_state.messages
                    ]
                    
                    try:
                        res = client.chat.completions.create(
                            model=active_model,
                            messages=groq_messages,
                            temperature=0.2,
                            max_tokens=150
                        )
                        reply = res.choices[0].message.content
                        st.markdown(reply)
                        st.session_state.messages.append({"role": "assistant", "content": reply})
                    except Exception as err:
                        st.error(f"Groq API Error: {err}")

        if st.button("🔄 Reset Chat Session"):
            st.session_state.messages = []
            st.rerun()

    # --- TAB 3: VISUAL ANALYTICS & CHARTS ---
    with tab3:
        st.subheader("📊 Session Performance Visualizers")
        
        c1, c2 = st.columns(2)
        with c1:
            fig_hist = px.histogram(
                df, 
                x="Spin of feature hit", 
                nbins=20, 
                title="Feature Hit Frequency (Spins Between Features)",
                color_discrete_sequence=["#6366F1"]
            )
            st.plotly_chart(fig_hist, use_container_width=True)
            
        with c2:
            fig_box = px.box(
                df, 
                x="Family", 
                y="Win multiplier", 
                title="Win Multiplier Distribution by Slot Family",
                color="Family"
            )
            st.plotly_chart(fig_box, use_container_width=True)

except Exception as e:
    st.error(f"Error loading app: {e}")
