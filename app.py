import streamlit as st
import pandas as pd
from datetime import datetime
import re
from streamlit_gsheets import GSheetsConnection

# ------------------------------------------
# PAGE CONFIG & CONSTANTS
# ------------------------------------------
st.set_page_config(
    page_title="Slot Session Tracker",
    page_icon="🎰",
    layout="wide"
)

SLOT_MASTER_LIST = {
    "Lightning Link": ["Sahara Gold", "Magic Pearl", "Happy Lantern", "High Stakes", "Heart Throb", "Tiki Fire"],
    "Dragon Link": ["Panda Magic", "Golden Century", "Happy & Prosperous", "Autumn Moon", "Peacock Princess", "Genghis Khan"],
    "Dragon Cash": ["Autumn Moon", "Golden Century", "Happy & Prosperous", "Panda Magic"]
}

# Initialize Google Sheets Connection
conn = st.connection("gsheets", type=GSheetsConnection)
SPREADSHEET_URL = st.secrets.get("connections", {}).get("gsheets", {}).get("spreadsheet", "")

# ------------------------------------------
# SESSION STATE INITIALIZATION
# ------------------------------------------
if "played_slots" not in st.session_state:
    st.session_state.played_slots = set()

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "📝 Live Data Entry"

def mark_slot_played(slot_name):
    st.session_state.played_slots.add(slot_name)

# ------------------------------------------
# NAVIGATION / TABS
# ------------------------------------------
st.title("🎰 Slot Machine Strategy & Session Tracker")

tabs = ["📝 Live Data Entry", "📊 Session Overview", "⚙️ Configuration"]
st.session_state.active_tab = st.radio("Navigation", tabs, horizontal=True)

st.markdown("---")

# ------------------------------------------
# TAB 1: LIVE DATA ENTRY (DIRECT GSHEETS WRITE)
# ------------------------------------------
if st.session_state.active_tab == "📝 Live Data Entry":
    st.subheader("📝 Live Session Data Entry")
    st.caption("Writes directly to your connected Google Sheet. Targets 'Session Log' by default.")

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

        # Configured strictly for "Session Log"
        target_worksheet = st.text_input("Worksheet / Tab Name in Google Sheet:", value="Session Log")

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
                # 1. Read existing records directly from 'Session Log'
                existing_data = conn.read(
                    spreadsheet=SPREADSHEET_URL if SPREADSHEET_URL else None,
                    worksheet=target_worksheet,
                    ttl="0"
                )
                
                # 2. Append new log entry safely
                if existing_data is not None and not existing_data.empty:
                    existing_data = existing_data.loc[:, ~existing_data.columns.str.contains('^Unnamed')]
                    updated_df = pd.concat([existing_data, new_gs_log], ignore_index=True)
                else:
                    updated_df = new_gs_log
                
                # 3. Write back to Google Sheets tab 'Session Log'
                conn.update(
                    spreadsheet=SPREADSHEET_URL if SPREADSHEET_URL else None,
                    worksheet=target_worksheet,
                    data=updated_df
                )
                
                mark_slot_played(entry_slot)
                st.success(f"✅ Successfully updated Google Sheets tab '{target_worksheet}'! Saved entry for '{entry_slot}'.")
            except Exception as e:
                st.error(f"Failed to update Google Sheets: {e}")

# ------------------------------------------
# TAB 2: SESSION OVERVIEW
# ------------------------------------------
elif st.session_state.active_tab == "📊 Session Overview":
    st.subheader("📊 Google Sheets Session History")
    
    target_worksheet = "Session Log"
    
    try:
        df_logs = conn.read(
            spreadsheet=SPREADSHEET_URL if SPREADSHEET_URL else None,
            worksheet=target_worksheet,
            ttl="0"
        )
        
        if df_logs is not None and not df_logs.empty:
            df_logs = df_logs.loc[:, ~df_logs.columns.str.contains('^Unnamed')]
            
            # Key metrics summary
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Recorded Hits", len(df_logs))
            m2.metric("Total Win ($)", f"${df_logs['Win Amount'].sum():,.2f}" if "Win Amount" in df_logs.columns else "$0.00")
            m3.metric("Played Slots Session", len(st.session_state.played_slots))
            
            st.dataframe(df_logs, use_container_width=True)
        else:
            st.info(f"No records found in '{target_worksheet}' tab.")
    except Exception as e:
        st.error(f"Could not load data from Google Sheets: {e}")

# ------------------------------------------
# TAB 3: CONFIGURATION & SECRETS CHECK
# ------------------------------------------
elif st.session_state.active_tab == "⚙️ Configuration":
    st.subheader("⚙️ Connection Status")
    
    if SPREADSHEET_URL:
        st.success("✅ `spreadsheet` key detected in Streamlit Secrets.")
        st.code(f"Target URL: {SPREADSHEET_URL}", language="text")
    else:
        st.warning("⚠️ `spreadsheet` URL not detected in `.streamlit/secrets.toml`.")
        st.markdown("""
        Ensure your `.streamlit/secrets.toml` includes:
        ```toml
        [connections.gsheets]
        spreadsheet = "[https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit](https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit)"
        ```
        """)
