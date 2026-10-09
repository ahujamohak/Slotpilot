import os
import re
import math
import numpy as np
import pandas as pd
from datetime import datetime
from collections import Counter, defaultdict
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from google import genai
from google.genai import types
try:
    from groq import Groq
except ImportError:
    Groq = None

# ==========================================
# 0. PAGE CONFIG & CONNECTION MANAGEMENT
# ==========================================
st.set_page_config(
    page_title="Slot Optimization & Execution Agent",
    layout="wide",
    initial_sidebar_state="auto",
)

# Minimal CSS — avoid clipping headers / metrics
st.markdown("""
<style>
    .block-container { padding-top: 2.5rem; padding-bottom: 2rem; max-width: 1100px; }
    .stButton > button { min-height: 2.75rem; border-radius: 10px; font-weight: 600; }
    .sug-card {
        border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px;
        background: #ffffff; margin-bottom: 0.6rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .sug-card.ai { border-left: 4px solid #7c3aed; }
    .sug-card.stat { border-left: 4px solid #2563eb; }
    @media (max-width: 640px) {
        .block-container { padding-left: 0.6rem; padding-right: 0.6rem; }
    }
</style>
""", unsafe_allow_html=True)

conn = st.connection("gsheets", type=GSheetsConnection)

GEMINI_MODEL = "gemini-3.6-flash"  # updated from gemini-2.5-flash
GROQ_MODEL = "openai/gpt-oss-20b"    # updated from deprecated llama-3.1-8b-instant

SESSION_STATE_WORKSHEET = "Live Session"
SESSION_LOG_WORKSHEET = "Session Log"
GAMBLE_WORKSHEET = "Gamble Log"

TAB_OPTIONS = [
    "🎯 Live Decision",
    "🃏 Gamble Analyzer",
    "📊 Today's Priority Board",
    "🧺 Session & Basket",
    "📚 Learnings",
]

SUITS = ["Hearts", "Diamonds", "Clubs", "Spades"]
SUIT_EMOJI = {"Hearts": "♥", "Diamonds": "♦", "Clubs": "♣", "Spades": "♠"}
SUIT_COLOR = {"Hearts": "Red", "Diamonds": "Red", "Clubs": "Black", "Spades": "Black"}
COLOR_EMOJI = {"Red": "🔴", "Black": "⚫"}

RED_SUITS = ["Hearts", "Diamonds"]
BLACK_SUITS = ["Clubs", "Spades"]

def suit_html(suit: str, size: str = "22px") -> str:
    color = "red" if SUIT_COLOR[suit] == "Red" else "#222"
    return f'<span style="color:{color}; font-size:{size}; font-weight:600;">{SUIT_EMOJI[suit]} {suit}</span>'

def color_html(color: str, size: str = "22px") -> str:
    col = "red" if color == "Red" else "#222"
    return f'<span style="color:{col}; font-size:{size}; font-weight:600;">{COLOR_EMOJI[color]} {color}</span>'

# ==========================================
# 0B. SESSION PERSISTENCE
# ==========================================
def load_persisted_state():
    try:
        df = conn.read(worksheet=SESSION_STATE_WORKSHEET, ttl="0")
        if df is None or df.empty:
            return None
        df.columns = [str(c).strip() for c in df.columns]
        return df.iloc[-1].to_dict()
    except Exception:
        return None

def persist_session_state():
    try:
        record = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Date": datetime.now().strftime("%m/%d/%Y"),
            "Current Bankroll": st.session_state.current_bankroll,
            "Starting Bankroll": st.session_state.session_start_bankroll,
            "Target Bankroll": st.session_state.session_target,
            "Selected Day": st.session_state.selected_day,
            "Strict Day Penalty": st.session_state.strict_day_penalty,
            "Played Basket": "|".join(st.session_state.played_basket),
        }
        conn.update(worksheet=SESSION_STATE_WORKSHEET, data=pd.DataFrame([record]))
        st.session_state.last_saved_ts = record["Timestamp"]
    except Exception as e:
        st.session_state.last_save_error = str(e)

def reset_all_state(wipe_persisted=True):
    st.session_state.played_basket = []
    st.session_state.display_limit = 30
    st.session_state.session_start_bankroll = 1250.0
    st.session_state.current_bankroll = 1250.0
    st.session_state.session_target = 1550.0
    st.session_state.stop_win = 300.0          # lock profit / soft stop when +this
    st.session_state.stop_loss = 1000.0        # hard stop when -this
    st.session_state.fade_gamble = False       # legacy; adaptive mode owns this
    st.session_state.gamble_fade_mode = "adaptive"  # adaptive | follow | fade
    st.session_state.active_tab = "🎯 Live Decision"
    st.session_state.strict_day_penalty = True
    st.session_state.chat_messages = []
    st.session_state.selected_day = datetime.now().strftime("%A")
    st.session_state.last_saved_ts = None
    st.session_state.last_save_error = None
    st.session_state.gamble_sequence = []
    st.session_state.ai_priority_result = None
    st.session_state.ai_gamble_suggestion = None
    if wipe_persisted:
        persist_session_state()


def session_profit_status():
    """Return locked profit and traffic-light status for the session."""
    start = float(st.session_state.get("session_start_bankroll", 1000) or 1000)
    current = float(st.session_state.get("current_bankroll", 1000) or 1000)
    target = float(st.session_state.get("session_target", 1800) or 1800)
    stop_win = float(st.session_state.get("stop_win", 400) or 400)
    stop_loss = float(st.session_state.get("stop_loss", 300) or 300)
    pnl = current - start
    if pnl <= -stop_loss:
        status = "STOP_LOSS"
        message = f"Stop-loss hit (−${abs(pnl):.0f}). Walk. Session over."
    elif pnl >= stop_win:
        status = "LOCK_PROFIT"
        message = f"Profit lock zone (+${pnl:.0f}). Only A-tier machines or leave."
    elif current >= target:
        status = "TARGET_HIT"
        message = f"Target reached (${current:.0f}). Strongly consider leaving."
    elif pnl > 0:
        status = "AHEAD"
        message = f"Ahead +${pnl:.0f}. Protect it — no hero calls."
    else:
        status = "BEHIND"
        message = f"Behind ${pnl:.0f}. Stick to plan; do not chase."
    return {
        "pnl": pnl,
        "status": status,
        "message": message,
        "start": start,
        "current": current,
        "target": target,
        "stop_win": stop_win,
        "stop_loss": stop_loss,
    }

if "played_basket" not in st.session_state:
    restored = load_persisted_state()
    today_str = datetime.now().strftime("%m/%d/%Y")
    if restored is not None and str(restored.get("Date", "")).strip() == today_str:
        basket_raw = str(restored.get("Played Basket", "") or "")
        st.session_state.played_basket = [s for s in basket_raw.split("|") if s]
        st.session_state.display_limit = 30
        st.session_state.session_start_bankroll = float(restored.get("Starting Bankroll", 1000.0) or 1000.0)
        st.session_state.current_bankroll = float(restored.get("Current Bankroll", 1000.0) or 1000.0)
        st.session_state.session_target = float(restored.get("Target Bankroll", 1800.0) or 1800.0)
        st.session_state.active_tab = "🃏 Gamble Analyzer"
        strict_raw = restored.get("Strict Day Penalty", True)
        st.session_state.strict_day_penalty = str(strict_raw).strip().lower() in ("true", "1", "yes")
        st.session_state.chat_messages = []
        st.session_state.selected_day = str(restored.get("Selected Day") or datetime.now().strftime("%A"))
        st.session_state.last_saved_ts = restored.get("Timestamp")
        st.session_state.last_save_error = None
        st.session_state.session_was_restored = True
    else:
        reset_all_state(wipe_persisted=False)
        st.session_state.session_was_restored = False

if "strict_day_penalty" not in st.session_state:
    st.session_state.strict_day_penalty = True
if "selected_day" not in st.session_state:
    st.session_state.selected_day = datetime.now().strftime("%A")
if "gamble_sequence" not in st.session_state:
    st.session_state.gamble_sequence = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "🃏 Gamble Analyzer"
if "ai_gamble_suggestion" not in st.session_state:
    st.session_state.ai_gamble_suggestion = None
if "stop_win" not in st.session_state:
    st.session_state.stop_win = 300.0
if "stop_loss" not in st.session_state:
    st.session_state.stop_loss = 1000.0
if "fade_gamble" not in st.session_state:
    st.session_state.fade_gamble = False
if "gamble_fade_mode" not in st.session_state:
    st.session_state.gamble_fade_mode = "adaptive"
# Ensure bankroll defaults if somehow missing
if "session_start_bankroll" not in st.session_state:
    st.session_state.session_start_bankroll = 1250.0
if "current_bankroll" not in st.session_state:
    st.session_state.current_bankroll = 1250.0
if "session_target" not in st.session_state:
    st.session_state.session_target = 1550.0
if "ai_priority_result" not in st.session_state:
    st.session_state.ai_priority_result = None

def mark_slot_played(slot_name: str) -> str:
    if slot_name not in st.session_state.played_basket:
        st.session_state.played_basket.append(slot_name)
        persist_session_state()
        return f"Successfully marked '{slot_name}' as played."
    return f"'{slot_name}' is already in the played basket."

def restore_slot(slot_name: str):
    if slot_name in st.session_state.played_basket:
        st.session_state.played_basket.remove(slot_name)
        persist_session_state()

# ==========================================
# 1. MASTER LIST & STRATEGY CONSTANTS
# ==========================================
STRATEGY_STEPS = [
    {"step": 1, "budget": 100, "denom": "$1.00", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 2, "budget": 100, "denom": "$0.10", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 3, "budget": 100, "denom": "$0.05", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 4, "budget": 100, "denom": "$0.02", "bet": 5.00, "spins": "20+ (Dynamic)"},
    {"step": 5, "budget": 100, "denom": "$0.01", "bet": 5.00, "spins": "20+ (Dynamic)"},
]
STRATEGY_PLAN_SUMMARY = "5 Denoms ($1 → 10c → 5c → 2c → 1c) @ Fixed $5 Bet ($100 budget / denom)"

SLOT_MASTER_LIST = {
    "All Aboard The Lucky Link": ["Go West", "Shinobi"],
    "Balloon Link": ["Australian Outback", "Skull Island"],
    "Bau Zhu Zhao Fu": ["Blue Festival", "Red Festival"],
    "Bull Rush Blitz": ["Golden Empress", "Wild Outback", "Yarr Matey"],
    "Bull Rush Blitz 2 Multi": ["Maximus Money", "New York Nights", "Roses & Riches"],
    "Bull Rush Blitz 3 Multi": ["El Matador"],
    "Bull Rush Stampede": ["Fire Mountain", "Minotaur’s Treasure"],
    "Cash Horns": ["Cleopatra’s Kingdom", "Grand Toro", "Master Warrior", "Ragnar the Great"],
    "Cash Spark": ["Royal Spark"],
    "Choy's Kingdom": ["Lunar Festival"],
    "Dollar Storm": ["Aussie Boomer", "Caribbean Gold", "Egyptian Jewels", "Fight for Troy", "Ninja Moon"],
    "Dragon Cash": ["Genghis Khan", "Magic Panda"],
    "Dragon Link": ["Autumn Moon", "Genghis Khan", "Golden Century", "Golden Gong", "Happy & Prosperous", "Panda Magic", "Peace & Long Life", "Peacock Princess", "Silk Road", "Spring Festival"],
    "Dragon Rush": ["Battle Drum", "Shadow Clan", "Shaolin Style"],
    "Dragon Train": ["Chillin Wins", "Forever Emperor", "Khutulun Battle Princess", "Sun Shots"],
    "Eureka n more blastin": ["Eureka n more blastin"],
    "Fabulous Hold & Spin Jackpot": ["Cash Champ", "Come one, Come all", "Glitter & Glitz", "Magic Touch"],
    "Fireball": ["Sea Queen Express", "Shogun Express"],
    "Fortune Hearts": ["Emperor's Choice", "Fire Spell", "Lunar Dragon"],
    "Go for Grand": ["Golden Sombreros", "Outback Gold", "Power Charms"],
    "Golden Strike": ["Viking Vallhala"],
    "Grand Legends": ["Great King", "Magic Warrior", "Royal Emperor", "Sun Queen"],
    "Heaven & Earth": ["Lucky Pig", "Shaolin Ways", "Terracotta Emperor"],
    "Huff n Even More Puff": ["Huff n Even More Puff"],
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
    "Thunder Empire": ["Amazon Hearts", "Inca Diamonds", "King Samurai", "Magic Emperor"],
    "Ultra Shot Link": ["Sapphire Eyes"],
    "Where's the Gold": ["Where's the Gold"],
    "Wild Rumble": ["Shen Shan"]
}

# Priority tiers from Oct-6 autopsy + full-log verification (1=best, 5=avoid)
# Applied to Priority Board ranking and play_style labels.
PRIORITY_TIER = {
    # Tier 1 — primary targets
    "Fire Mountain": 1,
    "Sun Shots": 1,
    "Autumn Moon": 1,
    "Ragnar the Great": 1,
    "El Matador": 1,
    "Minotaur’s Treasure": 1,
    # Tier 2 — strong / core volume
    "Maximus Money": 2,
    "Grand Toro": 2,
    "Enchanted Palace": 2,
    "Khan of Khans": 2,
    "Outback Gold": 2,
    "Peace & Long Life": 2,
    "Panda Magic": 2,
    "Master Warrior": 2,
    "Golden Gong": 2,
    "Cleopatra’s Kingdom": 2,
    # Tier 3 — situational
    "New York Nights": 3,
    "Magic Touch": 3,
    "King Samurai": 3,
    "Shadow Clan": 3,
    "Treasure Oasis": 3,
    "Come one, Come all": 3,
    "Royal Emperor": 3,
    "Golden Century": 3,
    "Fire Legend": 3,
    "Inca Diamonds": 3,
    "Go West": 3,
    "Emperor's Choice": 3,
    # Tier 4 — only if session up / strict caps
    "Lunar Dragon": 4,
    "Shaolin Style": 4,
    "Battle Drum": 4,
    "Glitter & Glitz": 4,
    "Forever Emperor": 4,
    # Tier 5 — deprioritise / skip
    "Amazon Hearts": 5,
    "Jelly Jams": 5,
}

TIER_LABEL = {
    1: "Primary target",
    2: "Strong — core list",
    3: "Situational",
    4: "Only if session up / strict cap",
    5: "Deprioritise / skip",
}

TIER_RANK_MULT = {1: 1.60, 2: 1.15, 3: 1.00, 4: 0.50, 5: 0.20}

# Fixed Friday sequence — no "or" options. Stop when +$500 profit hit.
FRIDAY_PLAY_ORDER = [
    # Refined from multi-Friday dry-runs (Aug–Oct 2026). Stop at +$500.
    "Fire Mountain",
    "El Matador",
    "Minotaur’s Treasure",
    "Enchanted Palace",
    "Grand Toro",
    "Autumn Moon",
    "Ragnar the Great",
    "Go West",
    "Cleopatra’s Kingdom",
    "King Samurai",
    "Shadow Clan",
    "Peace & Long Life",
    "Golden Gong",
    "Outback Gold",
    "Khan of Khans",
]


# Manual + data-backed play notes per slot.
# Each note: (text, confidence) confidence = High | Medium | Low
SLOT_NOTES = {
    "Maximus Money": {
        "family": "Bull Rush Blitz 2 Multi",
        "notes": [
            ("Max spins with no feature: 60. By then ~78% of historical features have already landed. Past 60, net+ rate collapses (~20%).", "High"),
            ("Check-in: $250 at $5 (assumes ~15% back in line wins → effective ~$4.25/spin).", "High"),
            ("After feature ≥30×: max spins on second hunt: 35. Then leave.", "High"),
            ("After feature <20×: max spins on second hunt: 20. Prefer leave.", "Medium"),
            ("89+ and 110+ blanks exceeded max spins — incorrect.", "High"),
            ("Denom: prefer 10c or $1 for the full hunt.", "Medium"),
        ],
    },
    "El Matador": {
        "family": "Bull Rush Blitz 3 Multi",
        "notes": [
            ("Max spins with no feature: 55.", "High"),
            ("Check-in: $225 at $5 (~15% line wins assumed).", "High"),
            ("After mega/large early (≥70×): max spins second hunt: 20. Then leave.", "High"),
            ("After medium early (~25–40×): max spins second hunt: 25. Leaving at 11 after 31× is conservative/OK.", "Medium"),
            ("After small (<20×): max spins second hunt: 15. Prefer leave.", "Medium"),
        ],
    },
    "Lunar Dragon": {
        "family": "Fortune Hearts",
        "notes": [
            ("High risk / slow. Do not open when session is losing.", "High"),
            ("Max spins with no feature: 70.", "High"),
            ("Check-in: $300 at $5 (~15% line wins assumed).", "High"),
            ("After feature <20×: leave (no second hunt). After ≥50× early: max second 30.", "High"),
            ("Watched AU $10 session (2026-10-08): feature at 165 (46×) and later 163× after heavy reloads. Late big pays exist — do NOT extend our max spins 70 or dig with reloads. Gamble lost multiple feature/line wins.", "High"),
        ],
    },
    "Minotaur’s Treasure": {
        "family": "Bull Rush Stampede",
        "notes": [
            ("After LARGE (>~55× e.g. 69×): rehit ~30%; max second hunt 20. Then leave.", "High"),
            ("Max spins with no feature: 70.", "High"),
            ("Check-in: $300 at $5 (~15% line wins assumed).", "High"),
            ("Bet: flat $5 full lines. On $1 denom Bull Rush is 1/3/5 lines only — play 5-line. Do not rotate 1/3/5 lines as a strategy.", "High"),
            ("Denom: $1 or 10c. Max 2–3 denoms. Same family as Fire Mountain but treat as its own machine.", "Medium"),
            ("Watched: $10 bet rotator on $1, ~60 spins blank, left. No feature data. Teases alone ≠ coming feature.", "Medium"),
        ],
    },
    "Battle Drum": {
        "family": "Dragon Rush",
        "notes": [
            ("Max spins with no feature: 60.", "Medium"),
            ("Check-in: $250 at $5 (~15% line wins assumed).", "Medium"),
            ("Feature <15× (e.g. 4×): leave — no real second hunt.", "High"),
            ("Denom: max 3 denoms, ~$75 each; prefer 10c/$1.", "Medium"),
        ],
    },
    "Enchanted Palace": {
        "family": "Mystery of the Lamp",
        "notes": [
            ("Features are Double / Active / Jackpot orbs (not classic scatter+orb). Log all as orb. Usually 1–3 specials open the feature.", "High"),
            ("Max spins no feature: 55. Check-in: $225. Bet flat $5.", "High"),
            ("Median features often medium (~20–40×). Back-to-back mediums are normal.", "High"),
            ("After small (<20×): max second 20. After medium (20–50×): max 20. After large (50×+): max 15. Then leave.", "High"),
            ("After 2–3 mediums if cabinet is ahead: leave. Do not open attempt 4 into a long blank.", "High"),
            ("Denom: 10c or $1 preferred at $5. Max 2–3 denoms. Watched hop 2c→5c→10c→$1 then 70+ dead on attempt 4 → zeroed a winning seat.", "High"),
            ("High orb frequency / line wins without feature = tease zone, not a reason to pass max spins.", "High"),
        ],
    },
    "Shaolin Style": {
        "family": "Dragon Rush",
        "notes": [
            ("Max spins with no feature: 60.", "High"),
            ("Check-in: $250 at $5 (~15% line wins assumed).", "High"),
            ("Line wins do not raise max spins above 60. No recovery extension.", "High"),
        ],
    },
    "Outback Gold": {
        "family": "Go for Grand",
        "notes": [
            ("Max spins no feature: 55. Check-in: $225. Bet flat $5.", "Medium"),
            ("Sample is thin. Watched first feature at 69 after reloads — outside profit-lock window. Do not dig to 70+ with reloads.", "High"),
            ("When it hits in chain: mediums (14×/25×/18× watched) then leave. After small max 20, after med max 25.", "Medium"),
            ("Denom: can show life on 2c then go quiet on $1. Prefer one sticky denom once chosen; max 2–3 total.", "Medium"),
            ("Early good orbs/lines that die mid-seat is common — not a reason to reload.", "High"),
        ],
    },
    "Amazon Hearts": {
        "family": "Thunder Empire",
        "notes": [
            ("Deprioritise — more walks than hits. Max spins: 35. No features after 35 in log.", "High"),
            ("Check-in: $150 at $5 (~15% line wins assumed). Skip if session red.", "High"),
            ("After any feature: max second 15. No 300-spin ladder.", "High"),
        ],
    },
    "Cleopatra’s Kingdom": {
        "family": "Cash Horns",
        "notes": [
            ("Max spins with no feature: 90. Can run late — not like Amazon.", "High"),
            ("Check-in: $375 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Ragnar the Great": {
        "family": "Cash Horns",
        "notes": [
            ("Max spins with no feature: 90. Workhorse; hit rate ~75%.", "High"),
            ("Check-in: $375 at $5 (~15% line wins assumed).", "High"),
            ("After ≥40×: max second hunt 40.", "High"),
        ],
    },
    "Khan of Khans": {
        "family": "Shenlong Unleashed",
        "notes": [
            ("Max spins with no feature: 55.", "High"),
            ("Check-in: $225 at $5 (~15% line wins assumed).", "High"),
            ("After ~50×: max second 25; prefer leave if ahead. Tuesday 51×@25 + ~21 leave = CORRECT.", "High"),
        ],
    },
    "Jelly Jams": {
        "family": "Fat Fortunes",
        "notes": [
            ("Very thin sample (n≈4). Treat as low confidence.", "High"),
            ("Max spins with no feature: 50.", "Low"),
            ("Check-in: $200 at $5 (~15% line wins assumed).", "Low"),
            ("Tuesday 102+ blank: exceeded any reasonable max spins — INCORRECT.", "High"),
        ],
    },
    "Forever Emperor": {
        "family": "Dragon Train",
        "notes": [
            ("Max spins with no feature: 55 (all features by 60 in log; p85 ~45).", "High"),
            ("Check-in: $225 at $5 (~15% line wins assumed).", "High"),
            ("Historical features often solid (median ~51×). Tuesday’s chain of small pays (21, 21, 2, 14) still lost the $250 buy-in — feature count ≠ profit.", "High"),
            ("After 3 features: if cabinet not ahead, leave. Do not run attempt 4–5 on crumbs.", "High"),
            ("After single feature <20×: max second hunt 20. Prefer leave.", "Medium"),
            ("Tuesday checkout $0 after multi small features: INCORRECT to stay through 5 attempts.", "High"),
        ],
    },
    "Fire Mountain": {
        "family": "Bull Rush Stampede",
        "notes": [
            ("Bet: flat $5. Do not ladder up from $1.25/$2.50. Big pays in log cluster on $1 at $3–$5.", "High"),
            ("Denom order: start $1. If dead after 25 spins on $1, switch to 5c for up to 25 more. Max 2 denoms. Then leave if still blank (max spins 60 total).", "High"),
            ("Max spins no feature: 60. Att1 features median ~32; walks often 60–70. Past 60 net+ collapses.", "High"),
            ("Check-in: $250.", "High"),
            ("After small (<20×): max 20 spins second hunt then leave. Seconds after small can be tiny.", "High"),
            ("After medium (20–50×): max 30 second hunt.", "High"),
            ("After large (50×+): max 35 second hunt. Rehit after large is common (~5/8 in log) but still cap it.", "High"),
            ("Watched AU 2026-10-08: weak 2× on 5c then 80× on 5c; separate seat $1×3 hit 1291× at spin 89 then 98× at 24. $1 is the money denom. Denom hoppers exist — we still cap at 2 denoms / 60 spins.", "High"),
            ("Watched sticky $1×$5: 56×@36 then 143×@36 then noise 3×/3× then 65×@10; 200→1000. Staying on $1 paid. First hit inside max 60. After large, second at 36 is 1 spin past our cap 35 — keep cap; do not stretch to 70.", "High"),
            ("Watched hopper 5c↔$1 with reloads: first 12×@42 then 52×@71; 300→200 loss. Long hunt + hop + reload underperformed sticky $1 plan.", "High"),
            ("After a big chain, 3× scatter noise is exit signal — do not fund attempt 4–6 chasing another large.", "High"),
        ],
    },
    "Sun Shots": {
        "family": "Dragon Train",
        "notes": [
            ("High avg mult (~70×) but multi only ~44%. Strong first feature, weaker chains.", "High"),
            ("Max spins with no feature: 75.", "High"),
            ("Check-in: $325 at $5 (~15% line wins assumed).", "High"),
            ("After big feature: max second 25 — multi is selective.", "Medium"),
        ],
    },
    "Autumn Moon": {
        "family": "Dragon Link",
        "notes": [
            ("Strong multi ~77%. Good continue-after-feature slot.", "High"),
            ("Max spins with no feature: 60.", "High"),
            ("Check-in: $250 at $5 (~15% line wins assumed).", "High"),
            ("After ≥25×: max second hunt 40.", "High"),
        ],
    },
    "Panda Magic": {
        "family": "Dragon Link",
        "notes": [
            ("Hit rate ~50%, multi ~71% when you get there. Medium priority.", "High"),
            ("Max spins with no feature: 70.", "High"),
            ("Check-in: $300 at $5 (~15% line wins assumed).", "High"),
        ],
    },
    "Grand Toro": {
        "family": "Cash Horns",
        "notes": [
            ("Solid Cash Horns volume. Hit ~69%, multi ~73%.", "High"),
            ("Max spins with no feature: 65.", "High"),
            ("Check-in: $275 at $5 (~15% line wins assumed).", "High"),
            ("After ≥40×: max second 40.", "Medium"),
        ],
    },
    "Master Warrior": {
        "family": "Cash Horns",
        "notes": [
            ("Volume machine. Median mult modest (~24×) — need size for profit.", "High"),
            ("Max spins with no feature: 65.", "High"),
            ("Check-in: $275 at $5 (~15% line wins assumed).", "High"),
        ],
    },
    "Royal Emperor": {
        "family": "Grand Legends",
        "notes": [
            ("Fast features (med spin ~14) but low median mult (~10×). Easy to hit, hard to profit big.", "High"),
            ("Max spins with no feature: 40.", "High"),
            ("Check-in: $175 at $5 (~15% line wins assumed).", "High"),
            ("After small feature: leave or max second 15.", "High"),
        ],
    },
    "New York Nights": {
        "family": "Bull Rush Blitz 2 Multi",
        "notes": [
            ("High avg mult (~77×) but multi only ~33%. Hunt feature, limited repeat.", "High"),
            ("Max spins with no feature: 70.", "Medium"),
            ("Check-in: $300 at $5 (~15% line wins assumed).", "Medium"),
            ("After feature: max second 25.", "Medium"),
        ],
    },
    "Golden Gong": {
        "family": "Dragon Link",
        "notes": [
            ("Hit ~73%, multi ~67%. Decent Dragon Link.", "Medium"),
            ("Max spins with no feature: 55.", "Medium"),
            ("Check-in: $225 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Peace & Long Life": {
        "family": "Dragon Link",
        "notes": [
            ("Max spins no feature: 60 (~91% of features by 60). Check-in: $250. Bet flat $5 on first hunt.", "High"),
            ("Multi-hit is real (~10/15 after first). Clusters happen — but median feature only ~24×. Cluster ≠ big.", "High"),
            ("Late first features (>=50 spins) in log are small/medium (4–41×), not jackpots. Do not treat a late first hit as a raise signal.", "High"),
            ("After small (<20×) or late first: stay $5 or leave. Max second 20. Do not jump to $7.50/$10.", "High"),
            ("After solid early first (>=30× and by spin ~40): max second 35 at $5. Optional one step to $7.50 only if session already up — not automatic $10.", "Medium"),
            ("Denom: $1 and 10c fine. Max 2–3 denoms.", "Medium"),
        ],
    },
    "Magic Touch": {
        "family": "Fabulous Hold & Spin Jackpot",
        "notes": [
            ("High hit rate ~76% but can run longer (p85 ~91). Multi ~67%.", "High"),
            ("Max spins with no feature: 90.", "Medium"),
            ("Check-in: $375 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Shadow Clan": {
        "family": "Dragon Rush",
        "notes": [
            ("Features by 60 almost always (95%). Max spins: 40.", "High"),
            ("Check-in: $175 at $5 (~15% line wins assumed).", "High"),
            ("Multi ~55%. Medium priority.", "Medium"),
        ],
    },
    "Treasure Oasis": {
        "family": "Mystery of the Lamp",
        "notes": [
            ("Median mult healthy (~49×). Can run longer.", "Medium"),
            ("Max spins with no feature: 80.", "Medium"),
            ("Check-in: $350 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Emperor's Choice": {
        "family": "Fortune Hearts",
        "notes": [
            ("Watched AU 2026-10-08: 41+ at $10 on $2, weak line wins, exit ~half check-in. Dry path is real.", "Medium"),
            ("Max spins with no feature: 80.", "Medium"),
            ("Check-in: $350 at $5 (~15% line wins assumed).", "Medium"),
            ("Multi ~50%. Related family to Lunar — don't use as recovery when red.", "Medium"),
        ],
    },
    "Golden Century": {
        "family": "Dragon Link",
        "notes": [
            ("Very early features (med ~11) but hit rate only 50% and low multi ~33%.", "High"),
            ("Max spins with no feature: 40.", "High"),
            ("Check-in: $175 at $5 (~15% line wins assumed).", "High"),
        ],
    },
    "Come one, Come all": {
        "family": "Fabulous Hold & Spin Jackpot",
        "notes": [
            ("Max spins with no feature: 55.", "Medium"),
            ("Check-in: $225 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "King Samurai": {
        "family": "Thunder Empire",
        "notes": [
            ("Avg mult solid (~53×). Max spins: 75.", "Medium"),
            ("Check-in: $325 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Inca Diamonds": {
        "family": "Thunder Empire",
        "notes": [
            ("Max spins with no feature: 80.", "Medium"),
            ("Check-in: $350 at $5 (~15% line wins assumed).", "Medium"),
            ("Multi only ~40% — limited repeat.", "Medium"),
        ],
    },
    "Fire Legend": {
        "family": "Thunder Empire",
        "notes": [
            ("Max spins with no feature: 75.", "Medium"),
            ("Check-in: $325 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Go West": {
        "family": "All Aboard The Lucky Link",
        "notes": [
            ("Max spins with no feature: 45.", "Medium"),
            ("Check-in: $200 at $5 (~15% line wins assumed).", "Medium"),
        ],
    },
    "Glitter & Glitz": {
        "family": "Fabulous Hold & Spin Jackpot",
        "notes": [
            ("Most features SMALL (~70% under 30×). Feature hit ≠ profit.", "High"),
            ("Max spins with no feature: 45 (~90% of features by 40).", "High"),
            ("Check-in: $200 at $5 (~15% line wins assumed).", "High"),
            ("After small (<20×): max second hunt 25. After two smalls: stop.", "Medium"),
        ],
    },
}

# Session learnings / knowledge base

VERIFIED_CLAIMS = [
    {"claim": "Outback Gold: reload to 300 to catch feature at spin 69", "source": "Watched 2026-10-08", "verdict": "REJECT for our plan", "evidence": "Feature came at 69 after three $100 loads. We use max 55 / $225 once. Accept missing some late first features.", "confidence": "High"},

    {"claim": "Enchanted Palace: after 3 medium features keep hunting attempt 4 while orbs are frequent", "source": "Watched 2026-10-08", "verdict": "INCORRECT", "evidence": "22x/20x/39x then 70+ blank to zero. Leave while ahead after 2–3 mediums. Max first-hunt 55.", "confidence": "High"},

    {"claim": "Bull Rush: rotate 1/3/5 lines on $1 to find feature", "source": "Watched Minotaur", "verdict": "NOT A STRATEGY", "evidence": "~60 spin blank while rotating lines at $10. Play fixed full lines at planned bet.", "confidence": "Medium"},

    {"claim": "Fire Mountain: sticky $1 at $5 better than denom hop + reload", "source": "Watched 2026-10-08", "verdict": "CORRECT", "evidence": "Sticky $1 session 200→1000 with 56x/143x/65x. Hopper reload session 300→200 with 12x then 52x@71.", "confidence": "High"},
    {"claim": "Fire Mountain: after large allow second hunt past 35 to catch 36-spin second", "source": "Watched 143x@36", "verdict": "NO rule change", "evidence": "One case 1 spin past cap. Stretching to 70 funds blanks. Keep after-large max 35.", "confidence": "Medium"},

    {"claim": "Friday: play many machines until tired", "source": "11 Sep pattern", "verdict": "INCORRECT", "evidence": "Marathon Fridays lose. Short A-tier + stop at +$500 wins (14 Aug, 18 Sep).", "confidence": "High"},
    {"claim": "Fire Mountain max spins 70 better than 60", "source": "You question", "verdict": "NOT SUPPORTED", "evidence": "Fri sample identical 60 vs 70. Late pays usually small. Keep 60 / $250.", "confidence": "High"},

    {"claim": "Peace & Long Life: late first feature still OK then raise to $7.50/$10 because of clusters", "source": "You theory", "verdict": "PARTLY WRONG", "evidence": "Multi-hit yes (~10/15). Late first features are small/medium (4–41x). Raise only after solid early first; first hunt stays $5 max spins 60.", "confidence": "High"},

    {"claim": "Fire Mountain: extend max spins to 90 because of 1291x at spin 89", "source": "You question + log", "verdict": "REJECTED", "evidence": "Late hits (>=70) without 1291 avg ~23x med ~15x. Most late features lose at $5. 1291 is outlier.", "confidence": "High"},
    {"claim": "Fire Mountain: leftover $50-75 after 60 spins means extend to 70-75", "source": "You question", "verdict": "NO as rule", "evidence": "Max spins 60 is spin-based not balance-based. Optional +10 only if active teases; else cash out runway.", "confidence": "High"},

    {"claim": "Lunar can pay after 100+ spins so extend max spins", "source": "Watched AU 2026-10-08", "verdict": "PARTLY TRUE but REJECT for our plan", "evidence": "46x at 165 and 163x at 65 after heavy reloads. Late/large pays exist. For $5 profit-lock play, max spins 70 and no dig still stand — hunting the tail requires bankroll we refuse.", "confidence": "High"},
    {"claim": "Gamble after feature locks profit", "source": "Watched AU 2026-10-08", "verdict": "INCORRECT", "evidence": "Lost $20, $300, and other gambles after features/line wins. Only log wins you keep.", "confidence": "High"},
    {"claim": "Emperor's Choice can dry ~40 spins with weak lines", "source": "Watched AU 2026-10-08", "verdict": "CORRECT", "evidence": "41+ at $10, check-in $500 → ~$270, no feature.", "confidence": "Medium"},

    {"claim": "Forever Emperor: keep playing through 4 small features to find a big one", "source": "You session", "verdict": "INCORRECT", "evidence": "21×, 21×, 2×, 14× then dry — checkout $0. After 3 features if not ahead, leave.", "confidence": "High"},

    {"claim": "Deprioritise Amazon Hearts (more walks than hits)", "source": "You", "verdict": "CORRECT", "evidence": "Hit rate ~44%, multi ~22%, no late feature window. Keep last on the list or skip.", "confidence": "High"},
    {"claim": "Khan of Khans Tuesday 51×@25 then ~21 leave", "source": "You session", "verdict": "CORRECT", "evidence": "Solid early medium-large; short second; locked profit (~+$86). Matches weak post-40–70× rehit sample.", "confidence": "High"},

    {"claim": "Amazon Hearts: reverse style — 300 spins at $5 then cluster and JJ", "source": "You theory", "verdict": "INCORRECT", "evidence": "0 features after spin 40 in log; only 2 second-features ever; no 3+ clusters. 300 spins funds walks, not a late cluster window.", "confidence": "High"},
    {"claim": "Cleopatra and Ragnar behave like Amazon Hearts (extreme dry then cluster)", "source": "You theory", "verdict": "INCORRECT", "evidence": "Cleo hit~71% with late features and multi-chains; Ragnar hit~75% with deep multi-hit. Different profile from Amazon 44% / no late features.", "confidence": "High"},

    {"claim": "Amazon Hearts needs ~200 spins / ladder bet up", "source": "You idea", "verdict": "INCORRECT", "evidence": "All 11 features in log by spin 35. Walks are long. Laddering into 100–200 spins is paying for dry spells, not a delayed feature window.", "confidence": "High"},
    {"claim": "Amazon Hearts: only $50 probe because high risk / low multi", "source": "You session", "verdict": "CORRECT", "evidence": "Hit rate ~44%, multi ~22%. Small check-in is the right size.", "confidence": "High"},
    {"claim": "Amazon Hearts pays early at $10 then goes quiet", "source": "You feel", "verdict": "NOT PROVEN", "evidence": "Almost no high-bet rows in log. Early-when-it-hits is true at $5; bet-size effect unknown.", "confidence": "Low"},

    {"claim": "Outback Gold: after 35× lower bet to $2.50 and ~23 spins to lock $100", "source": "You session", "verdict": "CORRECT", "evidence": "Early medium already profit. Seconds after medium often smaller. Lower bet + capped second hunt protects cash.", "confidence": "High"},

    {"claim": "El Matador: 31× on spin 1 then only 11 more spins", "source": "You session", "verdict": "CORRECT (conservative)", "evidence": "Early medium already profit. Thin sample shows rehit possible over ~25 spins; 11 locks profit and is slightly short if hunting a second, but not wrong.", "confidence": "Medium"},
    {"claim": "Maximus max spins 60", "source": "Data", "verdict": "CORRECT", "evidence": "78% features by 60; net+ after 60 ~20%. 89+ and 110+ exceeded max spins.", "confidence": "High"},

    {"claim": "Shaolin Style: stay past 100 spins because of big line wins", "source": "You session", "verdict": "INCORRECT", "evidence": "72% features by spin 40; late features (100+) in log were small (4–28×). Line wins cut burn but did not justify 124+; still −$400 no feature.", "confidence": "High"},

    {"claim": "Enchanted Palace: after two medium features, short third or leave (~39×, ~31×) at ~20 third spins", "source": "You session", "verdict": "CORRECT", "evidence": "Cabinet was already +EV. Third-attempt sample tiny; grinding for a bigger third risks giving back. Back-to-back mediums are a normal pattern (med mult ~32×).", "confidence": "High"},

    {"claim": "Maximus: most features by 50–60 spins; leave early", "source": "Partner", "verdict": "CORRECT", "evidence": "69% by 50, 78% by 60. Late (>60) net+ only 20% at $5 vs 62% early.", "confidence": "High"},
    {"claim": "Maximus: late wins are loss-making", "source": "Partner", "verdict": "CORRECT (net profit)", "evidence": "Late avg mult similar (~50x) but after spin cost net+ collapses.", "confidence": "High"},
    {"claim": "El Matador: short second hunt after mega/early big", "source": "You + notes", "verdict": "CORRECT (profit protect)", "evidence": "After >=70x rehit can still be high (~4/5 in log) but you already banked a mega — 15–25 spin second is enough; do not grind.", "confidence": "High"},
    {"claim": "Glitter: small features are normal; 80-spin hunt is wrong", "source": "You session", "verdict": "CORRECT", "evidence": "70% of features <30x; 90% of features by spin 40.", "confidence": "High"},
    {"claim": "Lunar: high risk / slow; do not play when already losing", "source": "Partner", "verdict": "CORRECT", "evidence": "Median feature ~38–49 spins; late features rarely net profit. Variance unsuitable for recovery play.", "confidence": "High"},
    {"claim": "Lunar: after 10x do not grind 100+", "source": "You session", "verdict": "CORRECT that grind was wrong", "evidence": "10x at 30 spins already net-negative at $5; 102+ empty is outside profitable region.", "confidence": "High"},
    {"claim": "Minotaur: after ~69x overall multi-hit ~53% applies", "source": "Earlier crude note", "verdict": "INCORRECT", "evidence": "After large (>$53x) rehit only ~29%; second avg ~32x. Size-conditional required.", "confidence": "High"},
    {"claim": "Minotaur: leave after early big at ~17 spins", "source": "You session", "verdict": "CORRECT", "evidence": "Matches low rehit / modest second after large.", "confidence": "High"},
    {"claim": "Battle Drum: 4x at spin 56 worth a real second hunt", "source": "You session", "verdict": "INCORRECT", "evidence": "Median feature ~29; 31% of features <15x; 4x is noise and late.", "confidence": "High"},
    {"claim": "Denom: hop every 5–10 spins helps", "source": "You habit", "verdict": "NOT PROVEN", "evidence": "No spin-by-spin denom trail in log. Cannot confirm or deny hop speed.", "confidence": "Low"},
    {"claim": "Denom: max 3 denoms ~$75 each then leave", "source": "Partner", "verdict": "PLAUSIBLE / good discipline", "evidence": "Not directly testable; aligns with not spraying weak denoms. Prefer slot top denoms.", "confidence": "Medium"},
    {"claim": "Line wins mean big feature coming", "source": "You observation", "verdict": "NOT PROVEN / often false", "evidence": "Lunar had line wins then small feature. No line-win column to test globally. Do not extend budget on line wins alone.", "confidence": "Medium"},
    {"claim": "Play machines that just paid others", "source": "Partner", "verdict": "NOT TESTABLE in log", "evidence": "No floor-context field. Soft signal only; size/spin rules still bind.", "confidence": "Low"},
]

LEARNINGS = [
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Shaolin 124+ on line wins", "detail": "124+ no feature; line wins ~$225 reduced burn but data says stop by 70–80. Late features historically small.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Maximus empty hunt length",
     "detail": "Played to 110+ with no feature. Data: ~78% features by spin 60. Hard-stop empty by 70–80.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Correct", "topic": "El Matador short second after mega",
     "detail": "Big on spin 5 then ~20 spins leave. Matches 20–25 max after large early.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Correct", "topic": "Minotaur early 69x then short chase",
     "detail": "Early solid feature then 17+ walk. Protect-early-profit rule.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Glitter long hunt for tiny features",
     "detail": "80 spins to 8x, then 15x, then 44 dry. 70% of features under 30x; 90% of features by spin 40.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Lunar Dragon 10x then 102+ grind",
     "detail": "10x at spin 30 already net-negative at $5; then 102+ empty. Partner: high risk, do not play when losing. Late features on this slot rarely profit.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Lunar Dragon defending 10x",
     "detail": "10x then 102+ empty. Small feature must not unlock a full second budget.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Session stop-loss not enforced",
     "detail": "Wanted +$300; finished about -$1250. After -$400–500 cut to tiny probes or leave.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Observation", "topic": "Partner: play machines that paid others",
     "detail": "Opened Glitter on that signal; got small features only. Soft positive — size/spin rules still rule.", "confidence": "Medium"},
    {"date": "2026-10-06", "status": "Observation", "topic": "Line wins vs feature quality",
     "detail": "Lunar: early line wins ($40–50) then small feature — line activity did not mean big feature. Maximus: no line wins and no feature same day. Track both; do not extend spin budget only because of line wins unless slot note says so.", "confidence": "Medium"},
    {"date": "2026-10-06", "status": "Correct", "topic": "Minotaur early 69x leave at 17+",
     "detail": "Early big then short second hunt. Data: second features avg only ~27x — leaving was correct.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Observation", "topic": "Denom hop speed (partner vs you)", "detail": "VERIFIED LIMIT: log only has Winning Denom on feature rows — cannot prove 5–10 spin hops vs $75/denom from data. VERIFIED: features spread across denoms; per-slot top denoms exist (e.g. Maximus 10c/$1, Minotaur 10c/$1 not 5c, Battle Drum 10c). Partner discipline (max 3 denoms, ~$75 each) is reasonable; avoid weak denoms for that slot.", "confidence": "Medium"},
    {"date": "2026-10-06", "status": "Observation", "topic": "Denom hop $50–75 then switch",
     "detail": "Player habit: $50–75 on $1 then 10c/1c/5c. Verify per slot; Minotaur favors 10c and $1 in log, not 5c.", "confidence": "Medium"},
    {"date": "2026-10-06", "status": "Incorrect", "topic": "Battle Drum 4x at spin 56", "detail": "4x is noise and late for this slot (median feature ~29). Should not fund a meaningful second hunt.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Correct", "topic": "Enchanted two mediums then 20+ leave", "detail": "39×@16 and 31×@15 then 20+. Net profit on cabinet. Had back-to-back mediums (normal for this slot); leaving at 20+ was correct.", "confidence": "High"},
        {"date": "2026-10-08", "status": "Correct", "topic": "Fire Mountain sticky $1 $5 session 200→1000", "detail": "56x@36, 143x@36, then 3x noise, 65x@10. No denom hop. Supports $1 primary and max 60 for first hit.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Incorrect", "topic": "Fire Mountain hop denoms + reload to force feature", "detail": "5c then $1 reloads; 12x@42 and 52x@71; checkout below check-in. Worse than sticky plan.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Minotaur watched $10 $1 line-rotate ~60 blank", "detail": "Load 500, $1 denom rotating $2/$6/$10 via lines, teases at 6/25/43, left ~60 no feature. Tease≠pay.", "confidence": "Medium"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Battle Drum watched 92 blank at $10", "detail": "10c then $1, frequent teases, big line win mid-way, still no feature by 92. Confirms not a Friday priority; leave at max 60.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Correct", "topic": "Shaolin early 36x then leave ~36 more", "detail": "10c $10 hit 36x@4; switched $1; left near spin 36 attempt 2 with profit. Matches short second after medium and no dig.", "confidence": "Medium"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Outback Gold first feature at 69 after reloads", "detail": "2c sticky after hops: 14x@69, 25x@24, 18x@11, leave attempt4. Late first needs dig we refuse. Max spins set 55.", "confidence": "Medium"},
    {"date": "2026-10-08", "status": "Incorrect", "topic": "Enchanted Palace long blank 102 with denom hop", "detail": "Second EP video: orbs and lines, still 102 to zero. Confirms max 55 and no attempt-4 dig.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Incorrect", "topic": "Enchanted Palace attempt-4 dig after three mediums", "detail": "Active 22x@14, Active 20x@11, Jackpot 39x@33, then 70+ on $1 to $0. Orb frequency stayed high — still dead. Leave while ahead.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Fire Mountain late features usually small", "detail": "Spin 70+ features mostly 2-59x except one 1291 outlier. Max spins stays 60. $1 denom primary, 5c secondary.", "confidence": "High"},
        {"date": "2026-10-08", "status": "Correct", "topic": "Friday plan: short A-tier list beats long grind",
     "detail": "18 Sep and 14 Aug style (few seats, hard exits) profit. 11 Sep marathon loses even with rules. Stop at +$500.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Correct", "topic": "Check-in sized to max spins not global 150 or 500",
     "detail": "Partner half-right: avoid vague middle+reload. Wrong as only two amounts. Use playbook check-in per slot.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Fire Mountain max spins stays 60 not 70/90",
     "detail": "Friday FM wins were early (1,6,44). 60 vs 70 identical on Fri sample. Late features usually small. Leftover $ after 60 = cash out not extend.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Fire Mountain denom $1 first then 5c",
     "detail": "Big mults cluster on $1. Max 2 denoms. Flat $5. No ladder from $1.25.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Incorrect", "topic": "Peace & Long Life late first then raise to $7.50/$10",
     "detail": "Multi real but late first features small/medium. First hunt flat $5 max 60. Raise only after solid early first if session up.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Emperor's Choice 41+ walk at $10 (watched AU)",
     "detail": "Flat $10 on $2 denom, check-in $500 → ~$270 exit at 41 spins, no feature, weak line wins. Confirms dry path exists; line wins cannot be assumed to fund long hunts.", "confidence": "Medium"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Lunar Dragon late 46x at spin 165 (watched AU $10)",
     "detail": "Feature can land very late (165) at 46x after heavy drain. Does NOT justify our $5 player grinding past max spins 70 — required large reloads. Late pay exists; expected session result of hunting it is still poor for profit-lock style.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Lunar Dragon 2x after long hunt then gamble loss",
     "detail": "82 spins to 2x scatter on 5c; gambled and lost. Small feature after long cost is pure damage. Matches rule: after <20x leave, do not gamble to recover.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Lunar 30x orb gambled away",
     "detail": "25 spins to 30x ($300 at $10); gambled and lost full amount. Feature win ≠ locked profit if gamble is on.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Observation", "topic": "Lunar 163x at spin 65 after multiple reloads",
     "detail": "Massive 1632 win ($10 bet) after prior blanks and reloads (~$500+). Shows high-vol upside is real AND that dig-out strategy needs bankroll we do not recommend. Not a template for $5 profit-lock play.", "confidence": "High"},
    {"date": "2026-10-08", "status": "Incorrect", "topic": "Deep reload ladder on Lunar while bleeding",
     "detail": "Watched player reloaded 400/300/500/500 while balance repeatedly hit near zero. Classic dig. Our rule: Lunar only if session up; no recovery ladder.", "confidence": "High"},
    {"date": "2026-10-06", "status": "Observation", "topic": "Net profit over feature count",
     "detail": "Machines can hit and still lose the buy-in. Score the day on cash, not scatters.", "confidence": "High"},
]

def get_slot_notes(slot_name: str) -> dict:
    """Return notes dict for a slot (case-insensitive), or empty."""
    if not slot_name:
        return {}
    key = str(slot_name).strip()
    if key in SLOT_NOTES:
        return SLOT_NOTES[key]
    for k, v in SLOT_NOTES.items():
        if k.lower() == key.lower():
            return v
    return {}


# Firm playbook — single numbers only (drives Live Decision card; no conflicting KM defaults)
# max_spins = leave if no feature by this count
# checkin = dollars at $5 with ~15% line wins
# after_small / after_med / after_large = max second-hunt spins after that size feature
SLOT_PLAYBOOK = {
    "Maximus Money": {"max_spins": 60, "checkin": 250, "after_small": 20, "after_med": 35, "after_large": 35, "tier": 2},
    "El Matador": {"max_spins": 55, "checkin": 225, "after_small": 15, "after_med": 25, "after_large": 20, "tier": 1},
    "Glitter & Glitz": {"max_spins": 45, "checkin": 200, "after_small": 25, "after_med": 25, "after_large": 25, "tier": 4},
    "Lunar Dragon": {"max_spins": 70, "checkin": 300, "after_small": 0, "after_med": 30, "after_large": 30, "tier": 4},
    "Minotaur’s Treasure": {"max_spins": 70, "checkin": 300, "after_small": 20, "after_med": 25, "after_large": 20, "tier": 1},
    "Battle Drum": {"max_spins": 60, "checkin": 250, "after_small": 0, "after_med": 15, "after_large": 15, "tier": 4},
    "Outback Gold": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 25, "after_large": 25, "tier": 2},
    "Amazon Hearts": {"max_spins": 35, "checkin": 150, "after_small": 15, "after_med": 15, "after_large": 15, "tier": 5},
    "Shaolin Style": {"max_spins": 60, "checkin": 250, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 4},
    "Enchanted Palace": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 20, "after_large": 15, "tier": 2},
    "Khan of Khans": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 25, "after_large": 25, "tier": 2},
    "Cleopatra’s Kingdom": {"max_spins": 90, "checkin": 375, "after_small": 25, "after_med": 40, "after_large": 40, "tier": 2},
    "Ragnar the Great": {"max_spins": 90, "checkin": 375, "after_small": 25, "after_med": 40, "after_large": 40, "tier": 1},
    "Jelly Jams": {"max_spins": 50, "checkin": 200, "after_small": 20, "after_med": 25, "after_large": 25, "tier": 5},
    "Forever Emperor": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 4},
    "Fire Mountain": {"max_spins": 60, "checkin": 250, "after_small": 20, "after_med": 30, "after_large": 35, "tier": 1, "denoms": ["$1", "5c"], "bet": 5},
    "Sun Shots": {"max_spins": 75, "checkin": 325, "after_small": 25, "after_med": 25, "after_large": 25, "tier": 1},
    "Autumn Moon": {"max_spins": 60, "checkin": 250, "after_small": 25, "after_med": 40, "after_large": 40, "tier": 1},
    "Panda Magic": {"max_spins": 70, "checkin": 300, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 2},
    "Grand Toro": {"max_spins": 65, "checkin": 275, "after_small": 25, "after_med": 40, "after_large": 40, "tier": 2},
    "Master Warrior": {"max_spins": 65, "checkin": 275, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 2},
    "Royal Emperor": {"max_spins": 40, "checkin": 175, "after_small": 15, "after_med": 20, "after_large": 20, "tier": 3},
    "New York Nights": {"max_spins": 70, "checkin": 300, "after_small": 20, "after_med": 25, "after_large": 25, "tier": 3},
    "Golden Gong": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 2},
    "Peace & Long Life": {"max_spins": 60, "checkin": 250, "after_small": 20, "after_med": 35, "after_large": 40, "tier": 2},
    "Magic Touch": {"max_spins": 90, "checkin": 375, "after_small": 25, "after_med": 40, "after_large": 40, "tier": 3},
    "Shadow Clan": {"max_spins": 40, "checkin": 175, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 3},
    "Treasure Oasis": {"max_spins": 80, "checkin": 350, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 3},
    "Emperor's Choice": {"max_spins": 80, "checkin": 350, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 3},
    "Golden Century": {"max_spins": 40, "checkin": 175, "after_small": 15, "after_med": 20, "after_large": 20, "tier": 3},
    "Come one, Come all": {"max_spins": 55, "checkin": 225, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 3},
    "King Samurai": {"max_spins": 75, "checkin": 325, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 3},
    "Inca Diamonds": {"max_spins": 80, "checkin": 350, "after_small": 20, "after_med": 30, "after_large": 30, "tier": 3},
    "Fire Legend": {"max_spins": 75, "checkin": 325, "after_small": 25, "after_med": 35, "after_large": 35, "tier": 3},
    "Go West": {"max_spins": 45, "checkin": 200, "after_small": 20, "after_med": 25, "after_large": 25, "tier": 3},
}

def get_playbook(slot_name: str) -> dict:
    if not slot_name:
        return {}
    key = str(slot_name).strip()
    if key in SLOT_PLAYBOOK:
        return SLOT_PLAYBOOK[key]
    for k, v in SLOT_PLAYBOOK.items():
        if k.lower() == key.lower():
            return v
    return {}

# ==========================================
# 2. SHEET DATA INSPECTION & METRICS ENGINE
# ==========================================
@st.cache_data(ttl=15)
def load_and_inspect_sheet():
    try:
        df = conn.read(worksheet=SESSION_LOG_WORKSHEET, ttl="0")
        if df.empty:
            return pd.DataFrame(), []
        df.columns = [str(c).strip() for c in df.columns]
        return df, list(df.columns)
    except Exception:
        return pd.DataFrame(), []

def parse_spin_value(raw):
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", ""):
        return None
    if s.endswith("+"):
        s = s[:-1]
    try:
        return int(float(re.sub(r"[^\d.]", "", s)))
    except Exception:
        return None

def get_spins_for_hit(slot_name, family_name, live_df, hit_number=1, percentile=85):
    if live_df.empty:
        return None
    cols = {str(c).lower().strip(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin")
    hit_col = cols.get("hit number") or cols.get("hit")
    feat_win_col = cols.get("feature win number")
    if not slot_col or not fam_col or not spin_col:
        return None
    df = live_df.copy()
    df = df[
        (df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
        (df[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
    ]
    if df.empty:
        return None
    if hit_col:
        df["_hit"] = pd.to_numeric(df[hit_col], errors="coerce")
        hits = df[df["_hit"] == hit_number]
    elif feat_win_col:
        df["_hit"] = pd.to_numeric(df[feat_win_col], errors="coerce")
        hits = df[df["_hit"] == hit_number]
    else:
        return None
    if hits.empty:
        return None
    spins = hits[spin_col].apply(parse_spin_value).dropna().astype(int)
    if len(spins) < 2:
        return None
    value = int(np.percentile(spins, percentile))
    value = int(round(value * 1.20))
    return value

def get_recommended_checkin(spin_1st, slot_name=None):
    """Prefer firm playbook check-in; else spin-budget heuristic."""
    if slot_name:
        pb = get_playbook(slot_name)
        if pb.get("checkin"):
            return int(pb["checkin"])
    if spin_1st is None:
        return 250
    if spin_1st <= 40:
        return 175
    elif spin_1st <= 55:
        return 225
    elif spin_1st <= 70:
        return 300
    elif spin_1st <= 90:
        return 375
    else:
        return 400

def parse_session_log_data(live_df, slot_name, family_name):
    if live_df.empty:
        return pd.DataFrame()
    cols = {str(c).lower().strip(): c for c in live_df.columns}
    slot_col = cols.get("slot") or cols.get("slot theme name") or cols.get("machine")
    fam_col = cols.get("family") or cols.get("slot family")
    spin_col = cols.get("spin of feature hit") or cols.get("spin") or cols.get("spins")
    attempt_col = cols.get("attempt number") or cols.get("attempt")
    feature_num_col = cols.get("feature win number") or cols.get("feature number") or cols.get("hit number")
    hit_num_col = cols.get("hit number") or cols.get("hit")
    win_amt_col = cols.get("win amount") or cols.get("win amount ($)") or cols.get("win")
    mult_col = cols.get("win multiplier") or cols.get("multiplier") or cols.get("win multiplier (x)")
    if not slot_col or not fam_col or not spin_col:
        return pd.DataFrame()
    df = live_df.copy()
    df = df[
        (df[slot_col].astype(str).str.strip().str.lower() == str(slot_name).strip().lower()) &
        (df[fam_col].astype(str).str.strip().str.lower() == str(family_name).strip().lower())
    ]
    if df.empty:
        return pd.DataFrame()
    def _parse_spin(raw):
        if pd.isna(raw):
            return np.nan, False
        s = str(raw).strip()
        is_censored = s.endswith("+")
        clean_s = s[:-1] if is_censored else s
        val = pd.to_numeric(re.sub(r"[^\d.]", "", clean_s), errors="coerce")
        return val, is_censored
    def _to_num(series):
        return pd.to_numeric(series.astype(str).str.extract(r"(\d+\.?\d*)")[0], errors="coerce")
    parsed_spins = df[spin_col].apply(_parse_spin)
    df["_spins"] = parsed_spins.apply(lambda x: x[0])
    df["_is_censored"] = parsed_spins.apply(lambda x: x[1])
    df["_attempt"] = _to_num(df[attempt_col]).fillna(1) if attempt_col else 1
    df["_feature_win_num"] = _to_num(df[feature_num_col]).fillna(0) if feature_num_col else 0
    df["_hit"] = _to_num(df[hit_num_col]).fillna(0) if hit_num_col else df["_feature_win_num"]
    df["_win"] = _to_num(df[win_amt_col]) if win_amt_col else 0.0
    df["_mult"] = _to_num(df[mult_col]) if mult_col else 0.0
    day_col = cols.get("day") or cols.get("day of week")
    df["_day"] = df[day_col].astype(str).str.strip() if day_col else ""
    return df

def compute_slot_rehit_metrics(slot_name, family_name, live_df):
    default_res = {
        "repeat_sample_size": 0, "attempt2_population": 0, "multi_hit_count": 0,
        "multi_hit_rate": 0.0, "avg_repeat_multiplier": 0.0, "max_repeat_multiplier": 0.0,
        "avg_attempt2_spins": 0.0, "repeat_recommendation": "No Repeat Data",
        "first_hit_count": 0, "first_hit_total": 0, "avg_first_multiplier": 0.0, "avg_first_spins": 0.0,
        "avg_third_multiplier": 0.0, "max_first_multiplier": 0.0,
    }
    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return default_res
    total_logs = len(parsed_df)
    for col in ["_feature_win_num", "_hit", "_attempt", "_mult", "_spins"]:
        if col in parsed_df.columns:
            parsed_df[col] = pd.to_numeric(parsed_df[col], errors="coerce")
    
    first_hits = parsed_df[(parsed_df["_feature_win_num"] == 1) & (parsed_df["_spins"].notna())]
    if first_hits.empty:
        first_hits = parsed_df[(parsed_df["_hit"] == 1) & (parsed_df["_attempt"] == 1) & (parsed_df["_spins"].notna())]
    first_hit_count = len(first_hits)
    avg_first_mult = round(float(first_hits["_mult"].mean()), 1) if not first_hits.empty else 0.0
    max_first_mult = round(float(first_hits["_mult"].max()), 1) if not first_hits.empty else 0.0
    valid_spins = first_hits["_spins"].dropna()
    avg_first_spins = round(float(valid_spins.mean()), 1) if not valid_spins.empty else 0.0

    repeat_entries = parsed_df[(parsed_df["_feature_win_num"] == 2)]
    if repeat_entries.empty:
        repeat_entries = parsed_df[(parsed_df["_hit"] == 2) & (parsed_df["_attempt"] == 2)]
    attempt2_rows = parsed_df[parsed_df["_attempt"] == 2]
    attempt2_population = len(repeat_entries) if attempt2_rows.empty and not repeat_entries.empty else len(attempt2_rows)
    repeat_count = len(repeat_entries)
    multi_hit_rate = round((repeat_count / attempt2_population) * 100.0, 1) if attempt2_population > 0 else 0.0
    avg_repeat_mult = round(repeat_entries["_mult"].mean(), 1) if not repeat_entries.empty else 0.0
    max_repeat_mult = round(repeat_entries["_mult"].max(), 1) if not repeat_entries.empty else 0.0
    att2_hits = repeat_entries[(repeat_entries["_spins"] > 0)]
    avg_att2_spins = round(att2_hits["_spins"].mean(), 1) if not att2_hits.empty else 0.0

    third_entries = parsed_df[(parsed_df["_feature_win_num"] == 3)]
    if third_entries.empty:
        third_entries = parsed_df[(parsed_df["_hit"] == 3)]
    avg_third_mult = round(third_entries["_mult"].mean(), 1) if not third_entries.empty else 0.0

    if attempt2_population == 0 and repeat_count == 0:
        recommendation = "ℹ️ UNTESTED REPEAT PROFILE"
    elif multi_hit_rate >= 40.0:
        recommendation = f"🔥 HIGH REPEAT POTENTIAL ({multi_hit_rate}%)"
    elif multi_hit_rate >= 20.0:
        recommendation = f"⚡ MODERATE REPEAT POTENTIAL ({multi_hit_rate}%)"
    else:
        recommendation = f"⚠️ LOW REPEAT POTENTIAL ({multi_hit_rate}%)"
    
    return {
        "repeat_sample_size": total_logs, "attempt2_population": attempt2_population,
        "multi_hit_count": repeat_count, "multi_hit_rate": multi_hit_rate,
        "avg_repeat_multiplier": avg_repeat_mult, "max_repeat_multiplier": max_repeat_mult,
        "avg_attempt2_spins": avg_att2_spins, "repeat_recommendation": recommendation,
        "first_hit_count": first_hit_count, "first_hit_total": total_logs,
        "avg_first_multiplier": avg_first_mult, "avg_first_spins": avg_first_spins,
        "avg_third_multiplier": avg_third_mult, "max_first_multiplier": max_first_mult,
    }

def compute_75_25_rvi(slot_name, family_name, live_df, target_day=None, strict_mode=True):
    baseline_score = 7.5
    if target_day is None:
        target_day = datetime.now().strftime("%A")
    parsed_df = parse_session_log_data(live_df, slot_name, family_name)
    if parsed_df.empty:
        return baseline_score, "25% Baseline / 0 Logs", target_day, 1.0, 0, 0
    total_logs = len(parsed_df)
    day_log_count = 0
    day_factor = 1.0
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    target_idx = days_order.index(target_day) if target_day in days_order else 0
    nearby_days = {days_order[target_idx], days_order[(target_idx - 1) % 7], days_order[(target_idx + 1) % 7]}
    if "_day" in parsed_df.columns:
        day_matches = parsed_df[parsed_df["_day"].str.lower() == str(target_day).strip().lower()]
        day_log_count = len(day_matches)
        nearby_matches = parsed_df[parsed_df["_day"].str.strip().str.title().isin(nearby_days)]
        nearby_count = len(nearby_matches)
        if total_logs > 0:
            day_ratio = day_log_count / total_logs
            if day_log_count > 0:
                if day_ratio == 1.0 and day_log_count >= 2:
                    day_factor = 1.30
                elif day_ratio >= 0.5:
                    day_factor = 1.20
                elif day_ratio >= 0.25:
                    day_factor = 1.10
                else:
                    day_factor = 1.00
            elif nearby_count > 0:
                day_factor = 0.95 if nearby_count >= 3 else 0.85
            else:
                if strict_mode:
                    day_factor = 0.55 if total_logs >= 5 else (0.70 if total_logs >= 3 else 0.80)
                else:
                    day_factor = 0.90
    actual_hits = parsed_df[(parsed_df["_hit"] > 0)]
    hit_count = len(actual_hits)
    if hit_count == 0:
        final_rvi = round(baseline_score * day_factor, 2)
        return final_rvi, f"Day-Weighted Hybrid (0 hits, {day_log_count} {target_day} logs)", target_day, day_factor, day_log_count, total_logs
    hit_rate = hit_count / total_logs
    hit_rate_score = min(10.0, max(1.0, hit_rate * 10.0))
    avg_win_mult = actual_hits["_mult"].mean()
    win_magnitude_score = min(10.0, max(1.0, (avg_win_mult / 15.0) + 5.0))
    sheet_rvi = (0.40 * hit_rate_score) + (0.60 * win_magnitude_score)
    weighted_rvi = (0.75 * sheet_rvi) + (0.25 * baseline_score)
    final_rvi = round(min(10.0, max(1.0, weighted_rvi * day_factor)), 2)
    proof_str = f"75% Live Sheet ({hit_count}/{total_logs} hits, {day_log_count} on {target_day}s)"
    return final_rvi, proof_str, target_day, day_factor, day_log_count, total_logs

def _derive_jj_tendency(profile: dict) -> tuple:
    """
    From a behaviour profile, derive a short JJ tendency label and a recommended play style.
    Returns (jj_tendency: str, play_style: str, post_big_note: str)
    """
    if not profile or not profile.get("ok"):
        return "Unknown", "Insufficient data", "—"

    multi_rate = profile.get("overall_multi_hit_rate", 0) or 0
    post = profile.get("post_win", {})
    sample_q = profile.get("sample_quality", "Low")
    clustering = profile.get("clustering_score", 0.5)

    small = post.get("small", {})
    medium = post.get("medium", {})
    large = post.get("large", {})

    small_rate = small.get("rehit_rate")
    med_rate = medium.get("rehit_rate")
    large_rate = large.get("rehit_rate")
    large_med_spins = large.get("median_spins_to_rehit")
    small_med_spins = small.get("median_spins_to_rehit")

    # JJ Tendency
    if sample_q == "Low":
        jj = "Unknown"
    elif small_rate is not None and small_rate >= 45 and (small_med_spins is not None and small_med_spins <= 35):
        jj = "Aggressive JJ"
    elif (small_rate or 0) >= 35 or (med_rate or 0) >= 40:
        jj = "Selective JJ"
    elif multi_rate >= 40:
        jj = "Moderate repeat"
    elif multi_rate >= 25:
        jj = "Low repeat"
    else:
        jj = "Rarely repeats"

    # Post-big-win note
    if large_rate is None:
        post_big = "—"
    elif large_rate < 25:
        post_big = f"Walk after big (re-hit only {large_rate}%)"
    elif large_rate < 40:
        post_big = f"Caution after big ({large_rate}% re-hit)"
    else:
        spins_txt = f", ~{large_med_spins} spins" if large_med_spins else ""
        post_big = f"Can continue after big ({large_rate}%{spins_txt})"

    # Recommended play style
    if sample_q == "Low":
        style = "Test lightly"
    elif jj == "Aggressive JJ" and multi_rate >= 45:
        style = "Primary target – hunt + JJ"
    elif jj in ("Aggressive JJ", "Selective JJ") and multi_rate >= 35:
        style = "Strong – look for JJ spots"
    elif multi_rate >= 40 and clustering < 0.55:
        style = "Solid grinder with repeats"
    elif multi_rate < 25 and (large_rate is not None and large_rate < 30):
        style = "One-and-done – take profit"
    elif multi_rate < 30:
        style = "Selective only"
    else:
        style = "Standard"

    return jj, style, post_big


def build_priority_dataset(live_df, target_day=None, strict_mode=True):
    records = []
    slot_scores = []
    if target_day is None:
        target_day = datetime.now().strftime("%A")

    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rvi_score, source_proof, active_day, day_factor, day_hits, total_hits = compute_75_25_rvi(slot, fam, live_df, target_day, strict_mode)
            rehit = compute_slot_rehit_metrics(slot, fam, live_df)

            # Legacy spin estimates (kept for compatibility)
            spin_1st = get_spins_for_hit(slot, fam, live_df, hit_number=1, percentile=85)
            spin_2nd = get_spins_for_hit(slot, fam, live_df, hit_number=2, percentile=85)
            spin_3rd = get_spins_for_hit(slot, fam, live_df, hit_number=3, percentile=85)

            # New behaviour profile (KM-aware, censored, post-win)
            profile = build_slot_behaviour_profile(fam, slot, live_df)
            jj_tendency, play_style, post_big_note = _derive_jj_tendency(profile)

            # Pooled all-features budget is the main cold-start number
            pooled = profile.get("pooled", {}) if profile.get("ok") else {}
            hn1 = profile.get("hit_numbers", {}).get(1, {}) if profile.get("ok") else {}
            hn2 = profile.get("hit_numbers", {}).get(2, {}) if profile.get("ok") else {}
            hn3 = profile.get("hit_numbers", {}).get(3, {}) if profile.get("ok") else {}

            budget_1st = pooled.get("km_p85") or pooled.get("p85") or hn1.get("km_p85") or hn1.get("p85") or spin_1st
            # 2nd/3rd kept for display but post-win size+timing is what Live Decision uses after a hit
            budget_2nd = hn2.get("km_p85") or hn2.get("p85") or spin_2nd
            budget_3rd = hn3.get("km_p85") or hn3.get("p85") or spin_3rd
            median_1st = pooled.get("median") or hn1.get("median")
            sample_quality = profile.get("sample_quality", "Low") if profile.get("ok") else "Low"
            multi_rate = profile.get("overall_multi_hit_rate", rehit.get("multi_hit_rate", 0.0)) if profile.get("ok") else rehit.get("multi_hit_rate", 0.0)

            first_total = rehit.get("first_hit_total", 0) or 0
            first_hits = rehit.get("first_hit_count", 0) or 0
            avg_mult = rehit.get("avg_first_multiplier", 0.0) or 0.0
            max_mult = rehit.get("max_first_multiplier", 0.0) or 0.0
            avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0) or 0.0
            avg_3rd_mult = rehit.get("avg_third_multiplier", 0.0) or 0.0
            max_2nd_mult = rehit.get("max_repeat_multiplier", 0.0) or 0.0

            if first_total < 3:
                composite = 0.0
            else:
                success_rate = first_hits / first_total if first_total > 0 else 0
                success_score = min(10.0, success_rate * 8.5)

                mult_score = min(13.0, (avg_mult / 5.0) + (max_mult / 22.0))

                # Prefer new budget for spin score
                s1 = budget_1st if budget_1st is not None else spin_1st
                if s1 is None:
                    spin_score = 4.5
                elif s1 <= 40:
                    spin_score = 9.0
                elif s1 <= 55:
                    spin_score = 7.0
                elif s1 <= 70:
                    spin_score = 4.8
                elif s1 <= 90:
                    spin_score = 2.5
                else:
                    spin_score = 1.0

                multi_size_bonus = min(4.5,
                    (avg_2nd_mult / 16.0) +
                    (max_2nd_mult / 30.0) +
                    (avg_3rd_mult / 20.0) +
                    (multi_rate / 45.0)
                )

                # Bonus for strong JJ tendency
                jj_bonus = 0.0
                if jj_tendency == "Aggressive JJ":
                    jj_bonus = 1.1
                elif jj_tendency == "Selective JJ":
                    jj_bonus = 0.6

                # Realized EV proxy: hit_rate * avg_mult (what actually paid historically)
                ev_proxy = (first_hits / first_total) * avg_mult if first_total > 0 else 0.0
                ev_score = min(12.0, ev_proxy / 4.0)  # ~48 EV → 12

                # Sample confidence: prefer slots with more history
                sample_score = min(10.0, first_total / 5.0)

                composite = (
                    0.32 * ev_score +          # primary: realized feature EV
                    0.18 * mult_score +
                    0.12 * success_score +
                    0.08 * spin_score +        # fast spins alone must not crown the board
                    0.15 * multi_size_bonus +
                    0.08 * jj_bonus * 10 +
                    0.07 * sample_score
                )

                # Hard sample gates — low-n slots must not top the board
                if first_total < 8:
                    composite *= 0.55
                elif first_total < 12:
                    composite *= 0.75
                elif first_total < 20:
                    composite *= 0.90
                if sample_quality == "Low":
                    composite *= 0.50
                # Low multi-hit should not rank as primary JJ targets
                if multi_rate < 30:
                    composite *= 0.70
                elif multi_rate < 40:
                    composite *= 0.88

            # Verified priority tiers (Oct-6 autopsy + log)
            tier = PRIORITY_TIER.get(slot, 3)
            composite *= TIER_RANK_MULT.get(tier, 1.0)
            # Day-of-week factor (was computed but not applied — fixed)
            composite *= float(day_factor) if day_factor else 1.0
            play_style = TIER_LABEL.get(tier, play_style)

            slot_scores.append({
                "family": fam,
                "slot": slot,
                "tier": tier,
                "rvi": rvi_score,
                "composite": round(composite, 3),
                "source_proof": source_proof,
                "target_day": active_day,
                "day_factor": day_factor,
                "day_hits": day_hits,
                "total_hits": total_hits,
                "rehit_metrics": rehit,
                "spin_1st": spin_1st,
                "spin_2nd": spin_2nd,
                "spin_3rd": spin_3rd,
                # New fields
                "budget_1st": budget_1st,
                "budget_2nd": budget_2nd,
                "budget_3rd": budget_3rd,
                "median_1st": median_1st,
                "multi_hit_rate": multi_rate,
                "jj_tendency": jj_tendency,
                "play_style": play_style,
                "post_big_note": post_big_note,
                "sample_quality": sample_quality,
                "behaviour_profile": profile,
            })

    slot_scores = sorted(slot_scores, key=lambda x: x["composite"], reverse=True)

    for item in slot_scores:
        records.append({
            "family": item["family"],
            "slot": item["slot"],
            "base_rvi": item["rvi"],
            "composite": item["composite"],
            "checkin_alloc": 500.0,
            "strategy_plan": STRATEGY_PLAN_SUMMARY,
            "source_proof": item["source_proof"],
            "target_day": item["target_day"],
            "day_factor": item["day_factor"],
            "day_hits": item["day_hits"],
            "total_hits": item["total_hits"],
            "rehit_metrics": item["rehit_metrics"],
            "spin_1st": item["spin_1st"],
            "spin_2nd": item["spin_2nd"],
            "spin_3rd": item["spin_3rd"],
            "budget_1st": item["budget_1st"],
            "budget_2nd": item["budget_2nd"],
            "budget_3rd": item["budget_3rd"],
            "median_1st": item["median_1st"],
            "multi_hit_rate": item["multi_hit_rate"],
            "jj_tendency": item["jj_tendency"],
            "play_style": item["play_style"],
            "tier": item.get("tier", 3),
            "post_big_note": item["post_big_note"],
            "sample_quality": item["sample_quality"],
            "behaviour_profile": item["behaviour_profile"],
        })
    return records

# ==========================================
# 2C. LIVE DECISION ENGINE  (fully per-slot, data-driven)
# ==========================================
def _parse_spin_censored(raw):
    """Return (spin_value: float|None, is_censored: bool). 52+ → (52.0, True)"""
    if pd.isna(raw):
        return None, False
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", ""):
        return None, False
    is_censored = s.endswith("+")
    clean = s[:-1] if is_censored else s
    try:
        val = float(re.sub(r"[^\d.]", "", clean))
        return val, is_censored
    except Exception:
        return None, False


def _kaplan_meier_percentile(event_times, censored_times, pct=0.85):
    """
    Simple Kaplan-Meier estimator.
    event_times: list of spin counts where a hit occurred.
    censored_times: list of spin counts where player walked with no hit.
    Returns the smallest t such that estimated CDF >= pct.
    Falls back gracefully on tiny samples.
    """
    if not event_times and not censored_times:
        return None

    # All unique time points
    all_times = sorted(set([t for t in event_times + censored_times if t is not None and t > 0]))
    if not all_times:
        return None

    # At risk process
    n_events = len(event_times)
    n_cens = len(censored_times)
    total = n_events + n_cens
    if total < 2:
        # Too little data – return observed max hit or a conservative default
        if event_times:
            return int(max(event_times) * 1.15)
        return None

    # Build survival curve
    # S(t) = product over ti <= t of (1 - d_i / n_i)
    from collections import defaultdict
    deaths = defaultdict(int)
    cens = defaultdict(int)
    for t in event_times:
        deaths[t] += 1
    for t in censored_times:
        cens[t] += 1

    times = sorted(set(list(deaths.keys()) + list(cens.keys())))
    n_at_risk = total
    S = 1.0
    survival = {}
    for t in times:
        d = deaths.get(t, 0)
        c = cens.get(t, 0)
        if n_at_risk > 0 and d > 0:
            S *= (1.0 - d / n_at_risk)
        survival[t] = S
        n_at_risk -= (d + c)

    # Find smallest t where 1 - S(t) >= pct
    target = pct
    for t in times:
        cdf = 1.0 - survival[t]
        if cdf >= target:
            return int(round(t))

    # Never reached the percentile → extrapolate a little beyond last event
    last_event = max(event_times) if event_times else max(all_times)
    return int(round(last_event * 1.25))


def _safe_percentile(values, pct, default=None):
    vals = [v for v in values if v is not None and not np.isnan(v)]
    if len(vals) < 2:
        return default
    return float(np.percentile(vals, pct))


def build_slot_behaviour_profile(family_name, slot_name, live_df):
    """
    Fully data-driven behaviour profile for one Family+Slot.
    Handles right-censored (+) observations correctly.
    Returns a rich dict used by the decision engine.
    """
    parsed = parse_session_log_data(live_df, slot_name, family_name)
    if parsed.empty:
        return {"ok": False, "reason": "No data for this slot"}

    # Normalise numeric columns
    for col in ["_feature_win_num", "_hit", "_attempt", "_mult", "_spins"]:
        if col in parsed.columns:
            parsed[col] = pd.to_numeric(parsed[col], errors="coerce")

    # Re-parse spins with censoring flag from original raw if available
    # (parse_session_log_data already gives _spins and _is_censored)
    if "_is_censored" not in parsed.columns:
        parsed["_is_censored"] = False

    profile = {
        "ok": True,
        "family": family_name,
        "slot": slot_name,
        "total_logs": len(parsed),
        "hit_numbers": {},
        "overall_multi_hit_rate": 0.0,
        "clustering_score": 0.0,          # 0 = regular, 1 = extreme clustering
        "post_win": {},                   # conditional stats after first win size
        "sample_quality": "Low",
    }

    # ---------- Per-hit-number distributions ----------
    for hn in [1, 2, 3, 4]:
        # Hits of this number
        hit_mask = (parsed["_hit"] == hn) | (parsed["_feature_win_num"] == hn)
        hits = parsed[hit_mask & (parsed["_spins"].notna()) & (~parsed["_is_censored"])]
        event_spins = hits["_spins"].dropna().astype(float).tolist()

        # Censored walk-offs that occurred on an attempt that was trying for this hit number
        # (Attempt Number == hn and Hit Number == 0 and censored)
        cens_mask = (
            (parsed["_attempt"] == hn) &
            (parsed["_hit"] == 0) &
            (parsed["_is_censored"] == True) &
            (parsed["_spins"].notna())
        )
        censored_spins = parsed.loc[cens_mask, "_spins"].astype(float).tolist()

        # Also treat non-censored zeros? No – only explicit + are walk-offs.

        n_events = len(event_spins)
        n_cens = len(censored_spins)
        n_total = n_events + n_cens

        if n_total < 2:
            profile["hit_numbers"][hn] = {
                "n_events": n_events, "n_censored": n_cens, "n_total": n_total,
                "median": None, "p75": None, "p85": None, "p90": None,
                "km_p85": None, "avg_mult": None, "max_mult": None,
            }
            continue

        # Observed (uncensored) percentiles
        med = _safe_percentile(event_spins, 50)
        p75 = _safe_percentile(event_spins, 75)
        p85 = _safe_percentile(event_spins, 85)
        p90 = _safe_percentile(event_spins, 90)

        # Kaplan-Meier style 85th (accounts for walk-offs)
        km85 = _kaplan_meier_percentile(event_spins, censored_spins, pct=0.85)

        # Multipliers for actual hits
        mults = hits["_mult"].dropna().astype(float).tolist()
        avg_m = round(float(np.mean(mults)), 1) if mults else None
        max_m = round(float(np.max(mults)), 1) if mults else None

        profile["hit_numbers"][hn] = {
            "n_events": n_events,
            "n_censored": n_cens,
            "n_total": n_total,
            "median": int(med) if med is not None else None,
            "p75": int(p75) if p75 is not None else None,
            "p85": int(p85) if p85 is not None else None,
            "p90": int(p90) if p90 is not None else None,
            "km_p85": km85,
            "avg_mult": avg_m,
            "max_mult": max_m,
            "event_spins": event_spins,
            "censored_spins": censored_spins,
        }

    # ---------- POOLED: all features as one process (+ walk-offs included) ----------
    # Every real feature hit counts; every "+" walk-off counts as "no feature by this spin"
    all_hit_mask = (
        ((parsed["_hit"] > 0) | (parsed["_feature_win_num"] > 0)) &
        (parsed["_spins"].notna()) &
        (~parsed["_is_censored"])
    )
    all_events = parsed.loc[all_hit_mask, "_spins"].astype(float).tolist()
    all_cens_mask = (
        (parsed["_is_censored"] == True) &
        (parsed["_spins"].notna())
    )
    all_censored = parsed.loc[all_cens_mask, "_spins"].astype(float).tolist()
    # Also: attempts with hit==0 and a spin count but not marked + (rare) — skip

    pooled_n_events = len(all_events)
    pooled_n_cens = len(all_censored)
    pooled_n = pooled_n_events + pooled_n_cens
    pooled_med = _safe_percentile(all_events, 50)
    pooled_p75 = _safe_percentile(all_events, 75)
    pooled_p85 = _safe_percentile(all_events, 85)
    pooled_km85 = _kaplan_meier_percentile(all_events, all_censored, pct=0.85)
    pooled_km50 = _kaplan_meier_percentile(all_events, all_censored, pct=0.50)
    all_mults = parsed.loc[all_hit_mask, "_mult"].dropna().astype(float).tolist()
    profile["pooled"] = {
        "n_events": pooled_n_events,
        "n_censored": pooled_n_cens,
        "n_total": pooled_n,
        "median": int(pooled_med) if pooled_med is not None else None,
        "p75": int(pooled_p75) if pooled_p75 is not None else None,
        "p85": int(pooled_p85) if pooled_p85 is not None else None,
        "km_p50": pooled_km50,
        "km_p85": pooled_km85,
        "avg_mult": round(float(np.mean(all_mults)), 1) if all_mults else None,
        "max_mult": round(float(np.max(all_mults)), 1) if all_mults else None,
        "event_spins": all_events,
        "censored_spins": all_censored,
        # Longest walk-off: "we know at least this many spins can pass with no feature"
        "max_walkoff": int(max(all_censored)) if all_censored else None,
    }

    # ---------- Overall multi-hit rate ----------
    att2 = parsed[parsed["_attempt"] == 2]
    second_hits = parsed[(parsed["_feature_win_num"] == 2) | ((parsed["_hit"] == 2) & (parsed["_attempt"] == 2))]
    n_att2_pop = len(att2) if len(att2) > 0 else len(second_hits)
    n_second = len(second_hits)
    profile["overall_multi_hit_rate"] = round(n_second / n_att2_pop * 100, 1) if n_att2_pop > 0 else 0.0
    profile["n_second_hits"] = n_second
    profile["n_attempt2_pop"] = n_att2_pop

    # ---------- Clustering score (from pooled gaps) ----------
    if len(all_events) >= 4:
        cv = float(np.std(all_events) / (np.mean(all_events) + 1e-6))
        profile["clustering_score"] = round(min(1.0, max(0.0, (cv - 0.4) / 1.2)), 2)
    else:
        profile["clustering_score"] = 0.5

    # ---------- Post-win: size (small/med/large) AND timing (early/late) ----------
    # Use ANY feature as "prior win", not only hit#1 — then next feature or walk-off
    any_hits = parsed[
        ((parsed["_hit"] > 0) | (parsed["_feature_win_num"] > 0)) &
        (parsed["_mult"] > 0) &
        (parsed["_spins"].notna()) &
        (~parsed["_is_censored"])
    ].copy()
    if len(any_hits) >= 4:
        mults = any_hits["_mult"].astype(float)
        spins_h = any_hits["_spins"].astype(float)
        q33 = float(mults.quantile(0.33))
        q66 = float(mults.quantile(0.66))
        spin_med = float(spins_h.median()) if len(spins_h) else 40.0

        def _size_bucket(m):
            if m <= q33:
                return "small"
            if m <= q66:
                return "medium"
            return "large"

        def _timing_bucket(sp):
            return "early" if sp <= spin_med else "late"

        # True continues only: attempt-1 feature then attempt-2 (hit or walk).
        # Chaining every feature in the log was inflating re-hit rates to ~100%.
        post = {}
        for size in ("small", "medium", "large"):
            for timing in ("early", "late"):
                post[f"{size}_{timing}"] = {"n": 0, "rehit": 0, "spins": [], "cens_spins": []}
            post[size] = {"n": 0, "rehit": 0, "spins": [], "cens_spins": []}

        firsts = parsed[
            ((parsed["_hit"] == 1) | (parsed["_feature_win_num"] == 1)) &
            (parsed["_mult"] > 0) &
            (parsed["_spins"].notna()) &
            (~parsed["_is_censored"])
        ]
        for idx, row in firsts.iterrows():
            size = _size_bucket(float(row["_mult"]))
            timing = _timing_bucket(float(row["_spins"]))
            key = f"{size}_{timing}"
            post[key]["n"] += 1
            post[size]["n"] += 1

            after = parsed[parsed.index > idx].head(10)
            cont = after[
                (after["_attempt"] == 2) |
                (after["_feature_win_num"] == 2) |
                (after["_hit"] == 2)
            ]
            if cont.empty:
                continue
            c0 = cont.iloc[0]
            is_cens = bool(c0["_is_censored"]) if pd.notna(c0.get("_is_censored")) else False
            hit_v = float(c0["_hit"]) if pd.notna(c0.get("_hit")) else 0.0
            fw = float(c0["_feature_win_num"]) if pd.notna(c0.get("_feature_win_num")) else 0.0
            if is_cens or (hit_v == 0 and fw < 2):
                if pd.notna(c0["_spins"]):
                    post[key]["cens_spins"].append(float(c0["_spins"]))
                    post[size]["cens_spins"].append(float(c0["_spins"]))
            elif hit_v > 0 or fw >= 2:
                if pd.notna(c0["_spins"]):
                    post[key]["rehit"] += 1
                    post[key]["spins"].append(float(c0["_spins"]))
                    post[size]["rehit"] += 1
                    post[size]["spins"].append(float(c0["_spins"]))

        def _pack(cell):
            n = cell["n"]
            r = cell["rehit"]
            spins = cell["spins"]
            cens = cell.get("cens_spins", [])
            # KM for rehit spin budget when we have censored post-win walks
            km = _kaplan_meier_percentile(spins, cens, pct=0.85) if (spins or cens) else None
            return {
                "n_first": n,
                "n_rehit": r,
                "rehit_rate": round(r / n * 100, 1) if n > 0 else None,
                "median_spins_to_rehit": int(_safe_percentile(spins, 50)) if len(spins) >= 2 else (int(np.median(spins)) if spins else None),
                "p75_spins_to_rehit": int(_safe_percentile(spins, 75)) if len(spins) >= 2 else None,
                "km_p85_rehit": km,
                "n_walkoffs_after": len(cens),
                "max_walkoff_after": int(max(cens)) if cens else None,
            }

        profile["post_win"] = {k: _pack(v) for k, v in post.items()}
        profile["mult_buckets"] = {"q33": round(q33, 1), "q66": round(q66, 1)}
        profile["spin_median_for_timing"] = int(round(spin_med))
    else:
        profile["post_win"] = {}
        profile["mult_buckets"] = {}
        profile["spin_median_for_timing"] = None

    # Sample quality from pooled feature count
    if pooled_n_events >= 20:
        profile["sample_quality"] = "High"
    elif pooled_n_events >= 10:
        profile["sample_quality"] = "Medium"
    else:
        profile["sample_quality"] = "Low"

    return profile


def decide_next_action(family_name, slot_name, attempt_num, spins_so_far, last_mult, current_bet, live_df):
    """
    Pure decision function.
    Returns a dict with Action, Bet advice, Max spins left, Confidence, Reason.
    Everything is derived from the slot's own history.
    """
    profile = build_slot_behaviour_profile(family_name, slot_name, live_df)
    if not profile.get("ok"):
        return {
            "action": "UNKNOWN",
            "bet_advice": "Insufficient data – play conservatively or switch",
            "max_spins_left": None,
            "confidence": "None",
            "reason": profile.get("reason", "No history for this slot"),
            "profile": profile,
        }

    attempt_num = int(attempt_num) if attempt_num else 1
    spins_so_far = float(spins_so_far) if spins_so_far is not None else 0.0
    last_mult = float(last_mult) if last_mult is not None and last_mult != "" else None
    current_bet = float(current_bet) if current_bet else 5.0

    # PRIMARY: pooled all-features distribution (treat every feature the same)
    pooled = profile.get("pooled", {})
    hn_info = profile["hit_numbers"].get(attempt_num, {})  # kept as secondary reference

    n_total = pooled.get("n_total") or hn_info.get("n_total", 0)
    n_events = pooled.get("n_events") or hn_info.get("n_events", 0)
    km85 = pooled.get("km_p85") or hn_info.get("km_p85")
    p75 = pooled.get("p75") or hn_info.get("p75")
    p85 = pooled.get("p85") or hn_info.get("p85")
    median = pooled.get("median") or hn_info.get("median")
    max_walkoff = pooled.get("max_walkoff")

    # Firm playbook max spins overrides KM when we have a verified number
    _pb = get_playbook(slot_name)
    upper = km85 or p85 or p75 or median
    if _pb.get("max_spins"):
        upper = int(_pb["max_spins"])
    if upper is None:
        upper = 60

    # If we have long walk-offs beyond upper, note them (do not auto-extend past KM)
    event_spins = pooled.get("event_spins") or hn_info.get("event_spins", [])
    cens_spins = pooled.get("censored_spins") or hn_info.get("censored_spins", [])
    still_alive = sum(1 for t in event_spins + cens_spins if t > spins_so_far)
    total_obs = len(event_spins) + len(cens_spins)
    pct_still_going = (still_alive / total_obs * 100) if total_obs > 0 else 50.0

    # ----- Decision logic (all thresholds derived from this slot) -----
    action = "STAY"
    bet_advice = f"Stay at ${current_bet:.2f}"
    max_left = max(0, int(upper - spins_so_far))
    confidence = profile["sample_quality"]
    reasons = []

    # 1. Already past the slot's own upper tail → WALK
    if spins_so_far >= upper and n_total >= 3:
        action = "WALK"
        bet_advice = "Walk – you are past this slot's historical upper range"
        max_left = 0
        reasons.append(f"Spins so far ({int(spins_so_far)}) ≥ slot's own 85th-percentile / KM estimate ({upper}).")
    elif pct_still_going < 15 and n_total >= 5:
        action = "WALK"
        bet_advice = "Walk – very few historical attempts lasted this long"
        max_left = 0
        reasons.append(
            f"Only {pct_still_going:.0f}% of past feature hunts on this slot lasted beyond {int(spins_so_far)} spins "
            f"(all features pooled; includes + walk-offs)."
        )
        if max_walkoff and spins_so_far >= max_walkoff:
            reasons.append(f"You are at or past the longest logged walk-off ({max_walkoff} spins).")

    # 2. Just hit a feature – use size + early/late timing (not hit#1 vs #2)
    elif last_mult is not None and last_mult > 0 and attempt_num >= 1:
        post = profile.get("post_win", {})
        buckets = profile.get("mult_buckets", {})
        q33 = buckets.get("q33")
        q66 = buckets.get("q66")
        spin_med = profile.get("spin_median_for_timing") or median or 40

        if q33 is not None and q66 is not None:
            if last_mult <= q33:
                size = "small"
            elif last_mult <= q66:
                size = "medium"
            else:
                size = "large"
        else:
            if last_mult <= 35:
                size = "small"
            elif last_mult <= 70:
                size = "medium"
            else:
                size = "large"

        # spins_so_far when logging a just-hit feature = how early/late that feature was
        timing = "early" if spins_so_far <= spin_med else "late"
        key = f"{size}_{timing}"
        bstats = post.get(key) or post.get(size) or {}
        rehit_rate = bstats.get("rehit_rate")
        med_rehit = bstats.get("median_spins_to_rehit")
        p75_rehit = bstats.get("p75_spins_to_rehit")
        km_rehit = bstats.get("km_p85_rehit")
        walk_after = bstats.get("max_walkoff_after")

        overall_rate = profile.get("overall_multi_hit_rate", 0)
        budget_next = km_rehit or p75_rehit or med_rehit or pooled.get("p75") or 40

        if rehit_rate is not None and bstats.get("n_first", 0) >= 2:
            if rehit_rate >= 45 and (med_rehit is not None and med_rehit <= 40):
                action = "JUDO JUMP"
                suggested = min(current_bet * 1.5, current_bet + 5) if current_bet < 10 else current_bet * 1.25
                suggested = round(suggested * 2) / 2
                bet_advice = f"Judo Jump – raise toward ${suggested:.2f} for next ~{budget_next} spins"
                max_left = int(budget_next)
                reasons.append(
                    f"After a {size} win that landed {timing} (vs this slot's median {spin_med} spins), "
                    f"re-hit rate was {rehit_rate}%, usually inside {med_rehit} spins."
                )
            elif rehit_rate < 25 and size == "large":
                action = "WALK"
                bet_advice = "Walk or drop bet – large wins here often cool the machine"
                max_left = 0
                reasons.append(
                    f"After large wins ({timing}) this slot re-hit only {rehit_rate}% of the time. "
                    f"Overall multi-hit rate {overall_rate}%."
                )
                if walk_after:
                    reasons.append(f"Logged walk-offs after similar wins went to {walk_after}+ spins with no feature.")
            else:
                action = "STAY"
                bet_advice = f"Stay at ${current_bet:.2f}"
                max_left = int(budget_next)
                reasons.append(
                    f"After {size}/{timing} wins re-hit rate is {rehit_rate}%. "
                    f"Same bet for about {max_left} spins is reasonable."
                )
        else:
            # Not enough conditional data – fall back to overall multi-hit rate
            if overall_rate >= 40:
                action = "STAY"
                bet_advice = f"Stay at ${current_bet:.2f} (overall multi-hit rate {overall_rate}%)"
                max_left = profile["hit_numbers"].get(min(attempt_num + 1, 4), {}).get("p75") or 40
                reasons.append(f"Overall multi-hit rate is solid ({overall_rate}%). Conditional data still thin.")
            else:
                action = "STAY"
                bet_advice = f"Stay or consider a smaller next bet (multi-hit rate only {overall_rate}%)"
                max_left = 30
                reasons.append(f"Limited conditional data and overall multi-hit rate is {overall_rate}%.")

    # 3. Still hunting for the current hit – normal continue / caution
    else:
        if spins_so_far < (median or 25):
            action = "STAY"
            bet_advice = f"Stay at ${current_bet:.2f} – still inside the typical window"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"Median for this hit number is ~{median}. You are at {int(spins_so_far)}.")
        elif spins_so_far < (p75 or upper):
            action = "STAY"
            bet_advice = f"Stay at ${current_bet:.2f} – approaching upper range, stay alert"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"You are between median and 75th percentile. Upper estimate ≈ {upper}.")
        else:
            action = "STAY"
            bet_advice = f"Caution – nearing historical limit. Max remaining ≈ {max(0, int(upper - spins_so_far))}"
            max_left = max(0, int(upper - spins_so_far))
            reasons.append(f"You are past the 75th percentile. Few attempts go much beyond {upper}.")

    # Prefer $5+ bets – soft note only
    if current_bet < 5.0 and action in ("STAY", "JUDO JUMP"):
        reasons.append("Note: you generally avoid bets below $5.")

    reason_text = " ".join(reasons) if reasons else "Based on this slot’s own history."

    return {
        "action": action,
        "bet_advice": bet_advice,
        "max_spins_left": max_left,
        "confidence": confidence,
        "reason": reason_text,
        "profile": profile,
        "upper_bound_used": upper,
        "pct_still_going": round(pct_still_going, 1),
    }

# ==========================================
# 2B. GAMBLE DATA ENGINE  (REPLACED – Variable-Order Markov)
# ==========================================
@st.cache_data(ttl=10)
def load_gamble_data():
    try:
        df = conn.read(worksheet=GAMBLE_WORKSHEET, ttl="0")
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()

def append_gamble_record(record: dict):
    try:
        existing = load_gamble_data()
        new_row = pd.DataFrame([record])
        if not existing.empty:
            for col in existing.columns:
                if col not in new_row.columns:
                    new_row[col] = ""
            for col in new_row.columns:
                if col not in existing.columns:
                    existing[col] = ""
            updated = pd.concat([existing.astype(str), new_row.astype(str)], ignore_index=True)
        else:
            updated = new_row.astype(str)
        conn.update(worksheet=GAMBLE_WORKSHEET, data=updated)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to write Gamble Log: {e}")
        return False


def build_gamble_log_record(
    seq,
    suggested_color,
    suggested_suit,
    actual_suit,
    source,
    *,
    faded=False,
    raw_model_color="",
    confidence="",
    match_len="",
    match_count="",
    pattern_pct="",
    fade_mode="",
    rolling_color_acc="",
    note="",
    provider="",
):
    """Full gamble row for later threshold / mode analysis."""
    now = datetime.now()
    act = str(actual_suit).strip().title()
    return {
        "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "Date": now.strftime("%m/%d/%Y"),
        "Day": now.strftime("%A"),
        "Card1": seq[0],
        "Card2": seq[1],
        "Card3": seq[2],
        "Card4": seq[3],
        "Card5": seq[4],
        "Sequence": "-".join(seq),
        "Suggested_Color": suggested_color,
        "Suggested_Suit": suggested_suit,
        "Actual_Next": act,
        "Actual_Color": SUIT_COLOR.get(act, ""),
        "Source": source,
        "Faded": "Y" if faded else "N",
        "Raw_Model_Color": raw_model_color or "",
        "Confidence": confidence or "",
        "Match_Len": match_len if match_len != "" else "",
        "Match_Count": match_count if match_count != "" else "",
        "Pattern_Pct": pattern_pct if pattern_pct != "" else "",
        "Fade_Mode": fade_mode or "",
        "Rolling_Color_Acc": rolling_color_acc if rolling_color_acc != "" else "",
        "Provider": provider or "",
        "Note": (note or "")[:200],
    }

def delete_gamble_records(timestamps_to_delete: list):
    """Delete specific rows from the Gamble Log by Timestamp and update the Google Sheet."""
    try:
        existing = load_gamble_data()
        if existing.empty or "Timestamp" not in existing.columns:
            return False
        ts_set = set(str(t).strip() for t in timestamps_to_delete)
        mask = ~existing["Timestamp"].astype(str).str.strip().isin(ts_set)
        updated = existing[mask].reset_index(drop=True)
        conn.update(worksheet=GAMBLE_WORKSHEET, data=updated)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to delete from Gamble Log: {e}")
        return False

def _parse_sequence_str(seq_str: str) -> list:
    if not seq_str or not isinstance(seq_str, str):
        return []
    parts = [p.strip() for p in seq_str.split("-") if p.strip()]
    return [p for p in parts if p in SUITS]

def _build_extended_sequence(current_seq: list, recent_df: pd.DataFrame) -> list:
    if recent_df.empty or "Sequence" not in recent_df.columns or len(current_seq) < 4:
        return current_seq[:]
    extended = current_seq[:]
    for _, row in recent_df.iloc[::-1].iterrows():
        prev = _parse_sequence_str(str(row.get("Sequence", "")))
        if len(prev) < 5:
            continue
        if extended[:4] == prev[-4:]:
            extended = prev[:-4] + extended
        else:
            break
    return extended[-12:] if len(extended) > 12 else extended


# ---------------------------------------------------------------------------
# VARIABLE-ORDER MARKOV ENGINE
# Tries longest context first (order 5 → 4 → 3 → 2 → 1 → 0).
# Uses a context only when it has been observed at least MIN_N times.
# This is the algorithm that achieved ~62% in-sample suit accuracy on the log.
# ---------------------------------------------------------------------------
MIN_N_BY_ORDER = {5: 2, 4: 2, 3: 2, 2: 3, 1: 4, 0: 1}

def _build_markov_model(df: pd.DataFrame) -> dict:
    """Build frequency tables for every order 0..5 from the full log."""
    model = {o: defaultdict(Counter) for o in range(0, 6)}
    if df is None or df.empty:
        return model
    for _, row in df.iterrows():
        cards = []
        for i in range(1, 6):
            c = str(row.get(f"Card{i}", "")).strip()
            if c in SUITS:
                cards.append(c)
        nxt = str(row.get("Actual_Next", "")).strip()
        if len(cards) < 5 or nxt not in SUITS:
            # fallback: try Sequence column
            seq = _parse_sequence_str(str(row.get("Sequence", "")))
            if len(seq) >= 5 and nxt in SUITS:
                cards = seq[-5:]
            else:
                continue
        for o in range(0, 6):
            key = tuple(cards[-o:]) if o > 0 else ()
            model[o][key][nxt] += 1
    return model

def _decide_from_counts(counts: Counter):
    """Return (top_suit, count_of_top, total, full_counter). Ties broken by highest count only."""
    if not counts:
        return "Hearts", 0, 0, Counter()
    top = counts.most_common(1)[0][0]
    k_top = counts[top]
    total = sum(counts.values())
    return top, k_top, total, counts

def _grade(n_seen: int, k_top: int, order: int) -> str:
    if n_seen == 0:
        return "None"
    share = k_top / n_seen
    if order >= 4 and n_seen >= 3 and share >= 0.70:
        return "Strong"
    if order >= 3 and n_seen >= 4 and share >= 0.60:
        return "Strong"
    if n_seen >= 5 and share >= 0.55:
        return "Moderate"
    if n_seen >= 3 and share >= 0.45:
        return "Moderate"
    return "Weak"

def _suggest_core(sequence: list, df: pd.DataFrame):
    """
    Variable-order Markov suggestion.
    Tries longest matching context first (5-card → … → 1-card → overall base rate).
    Returns a dict compatible with the existing UI.
    """
    model = _build_markov_model(df)
    base_counts = model[0][()]
    if not base_counts:
        return {
            "color": "Red", "suit": "Hearts", "context_len": len(sequence),
            "match_count": 0, "confidence": "None", "note": "No data yet",
            "color_strength": 50.0, "matched": False, "outcomes": {},
            "match_len": 0,
        }

    base_suit, base_n, base_total, _ = _decide_from_counts(base_counts)

    if len(sequence) < 1:
        return {
            "color": SUIT_COLOR[base_suit], "suit": base_suit, "context_len": 0,
            "match_count": 0, "confidence": "None", "note": "Enter at least 1 card",
            "color_strength": 50.0, "matched": False, "outcomes": {},
            "match_len": 0,
        }

    # Longest context first
    max_try = min(5, len(sequence))
    for order in range(max_try, -1, -1):
        key = tuple(sequence[-order:]) if order > 0 else ()
        counts = model[order].get(key, Counter())
        total = sum(counts.values())
        min_needed = MIN_N_BY_ORDER.get(order, 2)
        if total < min_needed:
            continue

        top, k_top, n_seen, full_counts = _decide_from_counts(counts)
        color = SUIT_COLOR[top]
        color_share = sum(c for s, c in full_counts.items() if SUIT_COLOR[s] == color) / n_seen
        conf = _grade(n_seen, k_top, order)
        times = "time" if n_seen == 1 else "times"
        len_label = f"{order}-card" if order > 0 else "base-rate"

        return {
            "color": color,
            "suit": top,
            "context_len": order,
            "match_count": n_seen,
            "confidence": conf,
            "matched": order > 0,
            "outcomes": dict(full_counts),
            "match_len": order,
            "note": (
                f"{len_label} context — seen {n_seen} {times}; next was {top} "
                f"in {k_top}/{n_seen} cases ({round(100 * k_top / n_seen)}%)."
            ),
            "color_strength": round(color_share * 100, 1),
        }

    # Absolute fallback (should never reach here if base rate exists)
    return {
        "color": SUIT_COLOR[base_suit], "suit": base_suit, "context_len": 0,
        "match_count": 0, "confidence": "None", "matched": False, "outcomes": {},
        "match_len": 0,
        "note": f"Showing overall most common suit ({base_suit}).",
        "color_strength": 50.0,
    }

def _pattern_color_rate(sug: dict) -> float:
    """Fraction of context outcomes that match the suggested colour (0–100)."""
    outcomes = sug.get("outcomes") or {}
    total = sum(outcomes.values()) or 0
    if total <= 0:
        return 0.0
    color = sug.get("color") or ""
    same = sum(n for s, n in outcomes.items() if SUIT_COLOR.get(s) == color)
    return 100.0 * same / total


def get_gamble_suggestion(sequence: list, fade_color: bool = False):
    """
    Public API – Variable-Order Markov.
    If fade_color=True, invert the recommended colour — UNLESS the local pattern
    is Strong with ≥70% one colour (do not fade a clear signal).
    """
    df = load_gamble_data()
    sug = _suggest_core(sequence, df)
    sug = dict(sug)
    sug["faded"] = False
    sug["fade_blocked"] = False

    if not fade_color:
        return sug

    # Do not fade a strong local colour signal (e.g. 3/4 Diamonds = 75% Red)
    rate = _pattern_color_rate(sug)
    conf = (sug.get("confidence") or "")
    if conf == "Strong" and rate >= 70:
        sug["fade_blocked"] = True
        sug["note"] = (
            f"FOLLOW kept — Strong pattern {rate:.0f}% {sug.get('color')}. "
            + (sug.get("note") or "")
        )
        return sug

    # Invert colour
    raw_color = sug.get("color") or "Red"
    faded_color = "Black" if raw_color == "Red" else "Red"
    outcomes = sug.get("outcomes") or {}
    opposite_suits = [s for s in SUITS if SUIT_COLOR[s] == faded_color]
    best_suit, best_n = opposite_suits[0], -1
    for s in opposite_suits:
        n = outcomes.get(s, 0)
        if n > best_n:
            best_suit, best_n = s, n
    sug["color"] = faded_color
    sug["suit"] = best_suit
    sug["faded"] = True
    sug["raw_color_before_fade"] = raw_color
    note = sug.get("note", "")
    sug["note"] = f"FADED (opposite of model). Model said {raw_color}. " + note
    return sug


def resolve_adaptive_fade(window: int = 40, fade_below: float = 45.0, follow_above: float = 55.0):
    """
    Decide whether to fade statistical colour from recent walk-forward accuracy.
    - recent model colour acc >= follow_above → FOLLOW (fade=False)
    - recent model colour acc <= fade_below → FADE (fade=True)
    - in between → FOLLOW (slight historical edge)
    Returns dict: fade, mode_label, recent_color_acc, recent_fade_acc, n, reason
    """
    bt = backtest_gamble_accuracy(window=window)
    if bt is None or bt.get("recent_color_acc") is None:
        return {
            "fade": False,
            "mode_label": "FOLLOW (insufficient data)",
            "recent_color_acc": None,
            "recent_fade_acc": None,
            "n": 0,
            "reason": "Need more logged rows for rolling accuracy. Default FOLLOW.",
            "window": window,
        }
    acc = float(bt["recent_color_acc"])
    fade_acc = bt.get("recent_fade_color_acc")
    n = int(bt.get("n_recent") or 0)
    if acc <= fade_below:
        return {
            "fade": True,
            "mode_label": "FADE (model cold)",
            "recent_color_acc": acc,
            "recent_fade_acc": fade_acc,
            "n": n,
            "reason": f"Last {n} model colour {acc}% ≤ {fade_below}% → bet opposite colour.",
            "window": window,
        }
    if acc >= follow_above:
        return {
            "fade": False,
            "mode_label": "FOLLOW (model hot)",
            "recent_color_acc": acc,
            "recent_fade_acc": fade_acc,
            "n": n,
            "reason": f"Last {n} model colour {acc}% ≥ {follow_above}% → follow model colour.",
            "window": window,
        }
    return {
        "fade": False,
        "mode_label": "FOLLOW (neutral band)",
        "recent_color_acc": acc,
        "recent_fade_acc": fade_acc,
        "n": n,
        "reason": f"Last {n} model colour {acc}% between {fade_below}–{follow_above}% → follow (slight edge).",
        "window": window,
    }


@st.cache_data(ttl=60)
def backtest_gamble_accuracy(window: int = 50):
    """
    Honest walk-forward backtest of the Variable-Order Markov engine.
    Each row is predicted using ONLY the rows that appeared before it.
    """
    df = load_gamble_data()
    if df is None or df.empty or "Actual_Next" not in df.columns:
        return None

    sequences = []
    for _, row in df.iterrows():
        cards = []
        for i in range(1, 6):
            c = str(row.get(f"Card{i}", "")).strip()
            if c in SUITS:
                cards.append(c)
        nxt = str(row.get("Actual_Next", "")).strip()
        if len(cards) == 5 and nxt in SUITS:
            sequences.append((cards, nxt))
        else:
            seq = _parse_sequence_str(str(row.get("Sequence", "")))
            if len(seq) >= 5 and nxt in SUITS:
                sequences.append((seq[-5:], nxt))

    warm_up = 40
    if len(sequences) < warm_up + 5:
        return None

    # Incremental indexes
    indexes = {o: defaultdict(list) for o in range(0, 6)}
    base = Counter()
    records = []

    for i, (cards5, actual) in enumerate(sequences):
        if i >= warm_up:
            pred = None
            matched = False
            grade = "None"
            match_len = 0
            for order in range(5, -1, -1):
                key = tuple(cards5[-order:]) if order > 0 else ()
                outs = indexes[order].get(key, [])
                min_needed = MIN_N_BY_ORDER.get(order, 2)
                if len(outs) >= min_needed:
                    counts = Counter(outs)
                    pred = counts.most_common(1)[0][0]
                    k_top = counts[pred]
                    matched = order > 0
                    grade = _grade(len(outs), k_top, order)
                    match_len = order
                    break
            if pred is None:
                pred = base.most_common(1)[0][0] if base else "Hearts"
            pred_color = SUIT_COLOR[pred]
            actual_color = SUIT_COLOR[actual]
            records.append({
                "color_correct": pred_color == actual_color,
                "fade_color_correct": pred_color != actual_color,  # opposite colour wins
                "suit_correct": pred == actual,
                "matched": matched,
                "grade": grade,
                "match_len": match_len,
            })

        # Update indexes with the current observation
        for order in range(0, 6):
            key = tuple(cards5[-order:]) if order > 0 else ()
            indexes[order][key].append(actual)
        base[actual] += 1

    bt = pd.DataFrame(records)
    recent = bt.tail(window)

    def acc(frame, col):
        return round(frame[col].mean() * 100, 1) if len(frame) else None

    matched_df = bt[bt["matched"]]
    unmatched_df = bt[~bt["matched"]]
    strong_df = bt[bt["grade"] == "Strong"]
    by_len = {}
    for n in (5, 4, 3, 2, 1):
        sub = bt[bt["match_len"] == n]
        by_len[n] = {"n": len(sub), "suit_acc": acc(sub, "suit_correct")}

    return {
        "n_total": len(bt),
        "n_recent": len(recent),
        "overall_color_acc": acc(bt, "color_correct"),
        "overall_suit_acc": acc(bt, "suit_correct"),
        "overall_fade_color_acc": acc(bt, "fade_color_correct"),
        "recent_color_acc": acc(recent, "color_correct"),
        "recent_suit_acc": acc(recent, "suit_correct"),
        "recent_fade_color_acc": acc(recent, "fade_color_correct"),
        "n_matched": len(matched_df),
        "matched_suit_acc": acc(matched_df, "suit_correct"),
        "matched_color_acc": acc(matched_df, "color_correct"),
        "n_unmatched": len(unmatched_df),
        "unmatched_suit_acc": acc(unmatched_df, "suit_correct"),
        "n_strong": len(strong_df),
        "strong_suit_acc": acc(strong_df, "suit_correct"),
        "baseline_color_acc": 50.0,
        "baseline_suit_acc": 25.0,
        "by_len": by_len,
    }

# ==========================================
# 3. AI AGENT ENGINE  (Gemini → Groq fallback)
# ==========================================
@st.cache_resource
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

@st.cache_resource
def get_groq_client():
    if Groq is None:
        return None
    api_key = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", None)
    if not api_key:
        return None
    return Groq(api_key=api_key)

def tool_mark_machine_played(slot_name: str) -> str:
    return mark_slot_played(slot_name)

def tool_update_bankroll(new_amount: float) -> str:
    st.session_state.current_bankroll = float(new_amount)
    persist_session_state()
    return f"Current bankroll updated to ${new_amount:.2f}"

AVAILABLE_TOOLS = {
    "tool_mark_machine_played": tool_mark_machine_played,
    "tool_update_bankroll": tool_update_bankroll,
}

def build_agent_context():
    available_slots = [
        s for s in st.session_state.slots_db
        if s["slot"] not in st.session_state.played_basket
        and (s.get("rehit_metrics", {}).get("first_hit_total", 0)) > 5
    ]
    slot_context_summary = []
    for s in available_slots[:20]:
        slot_context_summary.append({
            "slot": s["slot"], "family": s["family"], "rvi_score": s["base_rvi"],
            "multi_hit_rate": f"{s['rehit_metrics']['multi_hit_rate']}%",
            "multi_hit_count": s['rehit_metrics']['multi_hit_count'],
            "attempt2_population": s['rehit_metrics'].get('attempt2_population', 0),
            "strategy_plan": STRATEGY_PLAN_SUMMARY, "checkin_alloc": "$500",
            "recommendation_protocol": s['rehit_metrics']['repeat_recommendation']
        })
    system_instruction = f"""
    You are an expert AI Casino Slot Optimization & Execution Agent.
    CURRENT LIVE SESSION ENVIRONMENT:
    - Active Target Day: {st.session_state.selected_day}
    - Current Active Bankroll: ${st.session_state.current_bankroll:.2f}
    - Starting Bankroll: ${st.session_state.session_start_bankroll:.2f}
    - Target Bankroll: ${st.session_state.session_target:.2f}
    - Played Basket (Played Today): {st.session_state.played_basket}
    EXECUTION STRATEGY IN USE:
    - Check-in: $500 per machine across 5 denoms ($100 budget per denom).
    - Fixed Bet Denom Rotation: Always $5.00 bet per spin.
    - Exit Criteria: Book profit at $700+ or exit if back to $500.
    AVAILABLE TOP-RANKED SLOTS DATASET:
    {slot_context_summary}
    """
    return system_instruction

def run_gemini_agent(user_prompt: str):
    client = get_gemini_client()
    if not client:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    system_instruction = build_agent_context()
    contents = []
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))
    tools_list = [tool_mark_machine_played, tool_update_bankroll]
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools_list,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
    state_changed = False
    if response.function_calls:
        contents.append(response.candidates[0].content)
        function_response_parts = []
        for fn in response.function_calls:
            handler = AVAILABLE_TOOLS.get(fn.name)
            tool_result = handler(**(dict(fn.args) if fn.args else {})) if handler else f"Unknown tool '{fn.name}'."
            state_changed = True
            function_response_parts.append(
                types.Part.from_function_response(name=fn.name, response={"result": tool_result})
            )
        contents.append(types.Content(role="user", parts=function_response_parts))
        follow_up = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        return follow_up.text or "🤖 Action completed.", state_changed
    return response.text, state_changed

def run_groq_agent(user_prompt: str):
    client = get_groq_client()
    if not client:
        return "⚠️ Groq fallback unavailable."
    system_instruction = build_agent_context() + "\n\nNOTE: Text-only fallback mode."
    messages = [{"role": "system", "content": system_instruction}]
    for msg in st.session_state.chat_messages:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_prompt})
    completion = client.chat.completions.create(model=GROQ_MODEL, messages=messages, temperature=0.3)
    return completion.choices[0].message.content

def run_ai_agent(user_prompt: str):
    try:
        text, state_changed = run_gemini_agent(user_prompt)
        if state_changed:
            st.session_state.pending_rerun = True
        return text, "Gemini"
    except Exception as gemini_err:
        try:
            text = run_groq_agent(user_prompt)
            return f"{text}\n\n_(⚠️ Gemini fallback via Groq: {gemini_err})_", "Groq (fallback)"
        except Exception as groq_err:
            return f"⚠️ AI providers failed:\n- Gemini: {gemini_err}\n- Groq: {groq_err}", "None"

def parse_ai_gamble_response(text: str) -> dict:
    """Extract Colour and Suit from AI free-text response. Returns dict with color, suit, reason, ok."""
    if not text:
        return {"ok": False, "color": None, "suit": None, "reason": ""}
    color, suit, reason = None, None, ""
    # Colour / Color
    m = re.search(r"(?i)\bcolou?r\s*[:\-]\s*(red|black)\b", text)
    if m:
        color = m.group(1).title()
    # Suit
    m = re.search(r"(?i)\bsuit\s*[:\-]\s*(hearts|diamonds|clubs|spades)\b", text)
    if m:
        suit = m.group(1).title()
    # Fallback: look for bare suit words near end
    if not suit:
        for s in SUITS:
            if re.search(rf"(?i)\b{s}\b", text):
                suit = s
                break
    if suit and not color:
        color = SUIT_COLOR.get(suit)
    # Reason
    m = re.search(r"(?i)\breason\s*[:\-]\s*(.+)", text)
    if m:
        reason = m.group(1).strip().split("\n")[0][:200]
    ok = suit in SUITS and color in ("Red", "Black")
    return {"ok": ok, "color": color, "suit": suit, "reason": reason, "raw": text}


def get_ai_gamble_suggestion(sequence: list, extended: list):
    """Ask AI for a gamble suggestion. Tries Gemini first, falls back to Groq on any error.
    Returns (raw_text, provider, parsed_dict).
    """
    prompt = f"""
You are helping with a casino gamble feature (colour/suit prediction).

Current 5-card sequence: {' → '.join(sequence)}
Extended recent chain: {' → '.join(extended) if extended else 'N/A'}

Based on historical patterns, what is the most likely next card colour (Red/Black) and suit (Hearts/Diamonds/Clubs/Spades)?
Remember: Hearts & Diamonds = Red, Clubs & Spades = Black.

Reply in this exact format:
Colour: Red or Black
Suit: Hearts / Diamonds / Clubs / Spades
Reason: short explanation
"""

    text, provider = None, "None"
    # 1. Try Gemini
    try:
        client = get_gemini_client()
        if client:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            text = response.text or "No response"
            provider = "Gemini"
    except Exception as e:
        gemini_error = str(e)
        text = None
    else:
        gemini_error = "No Gemini client"

    # 2. Fallback to Groq
    if text is None:
        try:
            client = get_groq_client()
            if client:
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                )
                text = completion.choices[0].message.content
                provider = "Groq (fallback)"
        except Exception as e:
            text = f"AI error (both providers failed):\nGemini: {gemini_error}\nGroq: {e}"
            provider = "None"

    if text is None:
        text = "AI unavailable (no API keys configured)."
        provider = "None"

    parsed = parse_ai_gamble_response(text)
    return text, provider, parsed


def compute_ai_vs_stat_performance(window: int = 50):
    """
    From Gamble Log rows that have a Source column (or inferred),
    compute suit/colour accuracy for Statistical vs AI suggestions.
    """
    df = load_gamble_data()
    if df is None or df.empty or "Actual_Next" not in df.columns:
        return None

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # Normalise optional columns
    if "Source" not in df.columns:
        df["Source"] = "Statistical"  # legacy rows treated as statistical
    else:
        df["Source"] = df["Source"].fillna("Statistical").astype(str).str.strip()

    def _acc(sub):
        if sub.empty:
            return None, None, 0
        suit_ok = (sub["Suggested_Suit"].astype(str).str.strip().str.title() ==
                   sub["Actual_Next"].astype(str).str.strip().str.title())
        # colour from suit if needed
        def _col(s):
            s = str(s).strip().title()
            return SUIT_COLOR.get(s, "")
        color_ok = sub["Suggested_Suit"].map(_col) == sub["Actual_Next"].map(_col)
        n = len(sub)
        return (
            round(float(suit_ok.mean()) * 100, 1) if n else None,
            round(float(color_ok.mean()) * 100, 1) if n else None,
            n,
        )

    stat = df[df["Source"].str.lower().isin(["statistical", "stat", "markov", ""])]
    ai = df[df["Source"].str.lower().isin(["ai", "gemini", "groq"])]

    # Prefer most recent window
    stat_recent = stat.tail(window)
    ai_recent = ai.tail(window)

    s_suit, s_col, s_n = _acc(stat)
    s_suit_r, s_col_r, s_n_r = _acc(stat_recent)
    a_suit, a_col, a_n = _acc(ai)
    a_suit_r, a_col_r, a_n_r = _acc(ai_recent)

    return {
        "stat_suit_all": s_suit, "stat_color_all": s_col, "stat_n_all": s_n,
        "stat_suit_recent": s_suit_r, "stat_color_recent": s_col_r, "stat_n_recent": s_n_r,
        "ai_suit_all": a_suit, "ai_color_all": a_col, "ai_n_all": a_n,
        "ai_suit_recent": a_suit_r, "ai_color_recent": a_col_r, "ai_n_recent": a_n_r,
        "window": window,
    }

def get_ai_priority_ranking(slots_db, selected_day, played_basket):
    """Ask AI to re-rank machines. Tries Gemini first, falls back to Groq."""
    candidates = []
    for s in slots_db:
        if s["slot"] in played_basket:
            continue
        rehit = s.get("rehit_metrics", {})
        if rehit.get("first_hit_total", 0) < 5:
            continue
        candidates.append({
            "slot": s["slot"],
            "family": s["family"],
            "composite": s.get("composite", 0),
            "avg_mult": rehit.get("avg_first_multiplier", 0),
            "hit_rate": f"{rehit.get('first_hit_count',0)}/{rehit.get('first_hit_total',0)}",
            "spin_1st": s.get("spin_1st"),
            "multi_rate": rehit.get("multi_hit_rate", 0)
        })
    
    candidates = candidates[:60]
    
    prompt = f"""
You are an expert slot machine ranking engine for live casino play.

Target day: {selected_day}
Already played today: {played_basket}

Here are the current statistical candidates (top 60):
{candidates}

Re-rank the best 40-50 machines for today, prioritising:
1. High average multipliers
2. Good hit rates with decent sample size
3. Machines that have shown strong repeat potential
4. Avoid pure grinders that rarely pay big

Return ONLY a clean numbered list in this exact format (one per line):
1. Family | Slot Name
2. Family | Slot Name
...
Do not add extra text before or after the list.
"""

    # 1. Try Gemini
    try:
        client = get_gemini_client()
        if client:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            return response.text or "", "Gemini"
    except Exception as e:
        gemini_error = str(e)
    else:
        gemini_error = "No Gemini client"

    # 2. Fallback to Groq
    try:
        client = get_groq_client()
        if client:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            text = completion.choices[0].message.content
            return text, "Groq (fallback)"
    except Exception as e:
        return None, f"Both failed – Gemini: {gemini_error} | Groq: {e}"

    return None, "No AI providers available"

def parse_ai_priority_list(ai_text: str, slots_db: list):
    """Parse the AI numbered list back into slot records."""
    if not ai_text:
        return []
    
    lines = [l.strip() for l in ai_text.strip().splitlines() if l.strip()]
    parsed = []
    slot_lookup = {(s["family"].lower(), s["slot"].lower()): s for s in slots_db}
    
    for line in lines:
        match = re.match(r"^\d+[\.\)]\s*(.+?)\s*\|\s*(.+)$", line)
        if not match:
            continue
        fam = match.group(1).strip()
        slot = match.group(2).strip()
        
        key = (fam.lower(), slot.lower())
        if key in slot_lookup:
            parsed.append(slot_lookup[key])
        else:
            for s in slots_db:
                if s["slot"].lower() == slot.lower():
                    parsed.append(s)
                    break
    return parsed

# ==========================================
# LOAD DATA & INITIALIZE STATE
# ==========================================
SLOTS_DB_VERSION = 11  # Friday sequence refined multi-Friday dry-runs
live_sheet_df, detected_sheet_cols = load_and_inspect_sheet()
if (
    "slots_db" not in st.session_state
    or not st.session_state.slots_db
    or st.session_state.get("slots_db_version") != SLOTS_DB_VERSION
):
    st.session_state.slots_db = build_priority_dataset(
        live_sheet_df,
        st.session_state.selected_day,
        st.session_state.strict_day_penalty
    )
    st.session_state.slots_db_version = SLOTS_DB_VERSION

# ==========================================
# 4. SIDEBAR & NAVIGATION
# ==========================================
st.sidebar.title("🎰 Live Session Hub")
if st.session_state.get("session_was_restored"):
    st.sidebar.info("♻️ Restored active session data.")
if st.sidebar.button("🔄 Reset All Session Data", use_container_width=True, type="primary"):
    reset_all_state()
    st.rerun()

st.sidebar.markdown("---")
if detected_sheet_cols:
    st.sidebar.success(f"🟢 GSheet Connected ({len(detected_sheet_cols)} Cols)")
else:
    st.sidebar.warning("🟡 GSheet Off-line")

st.sidebar.subheader("📅 Day-of-Week Focus")
days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
current_day_idx = datetime.now().weekday()
default_day_idx = days_list.index(st.session_state.selected_day) if st.session_state.selected_day in days_list else current_day_idx
selected_day_input = st.sidebar.selectbox("Filter Target Day:", options=days_list, index=default_day_idx)
strict_penalty_toggle = st.sidebar.checkbox("Strict Day Match (Penalize 0-Hit Days)", value=st.session_state.strict_day_penalty)
if selected_day_input != st.session_state.selected_day or strict_penalty_toggle != st.session_state.strict_day_penalty:
    st.session_state.selected_day = selected_day_input
    st.session_state.strict_day_penalty = strict_penalty_toggle
    st.session_state.slots_db = build_priority_dataset(live_sheet_df, st.session_state.selected_day, st.session_state.strict_day_penalty)
    persist_session_state()
    st.rerun()

st.sidebar.subheader("📌 Navigation")
for tab_name in TAB_OPTIONS:
    is_active = (st.session_state.active_tab == tab_name)
    btn_type = "primary" if is_active else "secondary"
    if st.sidebar.button(tab_name, key=f"nav_btn_{tab_name}", use_container_width=True, type=btn_type):
        st.session_state.active_tab = tab_name
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Bankroll & Profit Lock")
with st.sidebar.form("bankroll_form"):
    new_start = st.number_input("Starting Bankroll ($)", value=float(st.session_state.session_start_bankroll), step=50.0)
    new_current = st.number_input("Current Bankroll ($)", value=float(st.session_state.current_bankroll), step=25.0)
    new_target = st.number_input("Target Bankroll ($)", value=float(st.session_state.session_target), step=100.0)
    new_stop_win = st.number_input("Stop-Win / Lock at +($)", value=float(st.session_state.stop_win), step=50.0)
    new_stop_loss = st.number_input("Stop-Loss at −($)", value=float(st.session_state.stop_loss), step=50.0)
    bankroll_submit = st.form_submit_button("💾 Update & Save")
    if bankroll_submit:
        st.session_state.session_start_bankroll = new_start
        st.session_state.current_bankroll = new_current
        st.session_state.session_target = new_target
        st.session_state.stop_win = new_stop_win
        st.session_state.stop_loss = new_stop_loss
        persist_session_state()
        st.rerun()

_sp = session_profit_status()
st.sidebar.metric("Locked P&L", f"${_sp['pnl']:+.0f}")
if _sp["status"] in ("STOP_LOSS",):
    st.sidebar.error(_sp["message"])
elif _sp["status"] in ("LOCK_PROFIT", "TARGET_HIT"):
    st.sidebar.success(_sp["message"])
elif _sp["status"] == "AHEAD":
    st.sidebar.info(_sp["message"])
else:
    st.sidebar.warning(_sp["message"])

st.sidebar.markdown("---")
st.sidebar.subheader("🃏 Gamble mode")
_fade_opts = ["adaptive", "follow", "fade"]
_fade_labels = {
    "adaptive": "Adaptive (auto 45/55)",
    "follow": "Always FOLLOW model",
    "fade": "Always FADE (opposite)",
}
_cur = st.session_state.get("gamble_fade_mode", "adaptive")
if _cur not in _fade_opts:
    _cur = "adaptive"
_sel = st.sidebar.radio(
    "Colour mode",
    options=_fade_opts,
    index=_fade_opts.index(_cur),
    format_func=lambda x: _fade_labels[x],
    help="Adaptive: fade if last ~40 model colour ≤45%; follow if ≥55%; else follow.",
)
st.session_state.gamble_fade_mode = _sel
# Keep legacy flag in sync for any old references
if _sel == "fade":
    st.session_state.fade_gamble = True
elif _sel == "follow":
    st.session_state.fade_gamble = False
else:
    st.session_state.fade_gamble = resolve_adaptive_fade().get("fade", False)

st.sidebar.markdown("---")
st.sidebar.subheader("✅ Quick Mark Played")
qm_family = st.sidebar.selectbox("Family:", options=list(SLOT_MASTER_LIST.keys()), key="qm_fam")
qm_slot = st.sidebar.selectbox("Slot:", options=SLOT_MASTER_LIST[qm_family], key="qm_slot")
if st.sidebar.button("Mark as Played", use_container_width=True):
    res = mark_slot_played(qm_slot)
    st.sidebar.success(res)
    st.rerun()

# ==========================================
# 5. DASHBOARD VIEWS
# ==========================================

# Session P&L lives in the sidebar only — no duplicate strip on every page

if st.session_state.active_tab == "🎯 Live Decision":
    st.subheader("🎯 Live Decision Engine")
    st.caption(
        "All feature wins on this slot are pooled for spin budgets (+ walk-offs included). "
        "After a win, advice uses win size and whether it landed early or late."
    )

    # Build family → slots map from master list + any extra seen in data
    all_families = sorted(SLOT_MASTER_LIST.keys())
    col_a, col_b = st.columns(2)
    with col_a:
        sel_family = st.selectbox("Family", options=all_families, key="ld_family")
    with col_b:
        slot_opts = SLOT_MASTER_LIST.get(sel_family, [])
        sel_slot = st.selectbox("Slot", options=slot_opts, key="ld_slot")

    # One firm card — playbook numbers only (no competing KM defaults)
    _pb = get_playbook(sel_slot)
    _tier = PRIORITY_TIER.get(sel_slot, _pb.get("tier", 3))
    st.markdown("#### Play plan")
    if _pb:
        c1, c2, c3 = st.columns(3)
        c1.metric("Bet", "$5")
        c2.metric("Check-in", f"${_pb['checkin']}")
        c3.metric("Max spins (no feature)", f"{_pb['max_spins']}")
        st.write(
            f"Tier {_tier}: {TIER_LABEL.get(_tier, 'Standard')}. "
            f"After small feature (under 20x): max {_pb['after_small']} spins more then leave"
            + (" (leave now)" if _pb['after_small'] == 0 else "")
            + f". After medium (20–50x): max {_pb['after_med']} spins. "
            f"After large (50x+): max {_pb['after_large']} spins. "
            f"Denom: up to 3 denoms, about $75 each, then leave if dead. Prefer historically strong denoms for this slot."
        )
        if _tier >= 5:
            st.warning("Deprioritised slot — skip unless nothing else is available.")
        elif _tier == 4:
            st.info("Only play if session is flat or up. Strict caps.")
    else:
        st.info("No firm playbook for this slot yet. Use check-in $200 and max spins 60.")
    _extra = get_slot_notes(sel_slot)
    if _extra and _extra.get("notes"):
        with st.expander("More detail"):
            for text, conf in _extra["notes"]:
                st.write(f"{text} ({conf})")

    st.markdown("#### Current situation at the machine")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        attempt_in = st.number_input("Attempt # (1 = first feature hunt)", min_value=1, max_value=15, value=1, step=1, key="ld_attempt")
    with c2:
        spins_in = st.number_input("Spins already played this attempt", min_value=0, max_value=500, value=0, step=1, key="ld_spins")
    with c3:
        last_mult_in = st.number_input("Last feature multiplier (0 if none yet)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0, key="ld_mult")
    with c4:
        bet_in = st.number_input("Current bet ($)", min_value=0.5, max_value=50.0, value=5.0, step=0.5, key="ld_bet")

    if st.button("Get Recommendation", type="primary", use_container_width=True, key="ld_run"):
        with st.spinner("Building per-slot behaviour profile from history…"):
            result = decide_next_action(
                family_name=sel_family,
                slot_name=sel_slot,
                attempt_num=attempt_in,
                spins_so_far=spins_in,
                last_mult=last_mult_in if last_mult_in > 0 else None,
                current_bet=bet_in,
                live_df=live_sheet_df,
            )

        st.markdown("---")
        action = result["action"]
        color_map = {
            "STAY": "blue",
            "JUDO JUMP": "green",
            "WALK": "red",
            "DROP BET": "orange",
            "UNKNOWN": "gray",
        }
        st.markdown(f"### Recommendation: :{color_map.get(action, 'gray')}[{action}]")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Action", action)
        m2.metric("Max spins left", result["max_spins_left"] if result["max_spins_left"] is not None else "—")
        m3.metric("Confidence", result["confidence"])
        m4.metric("Upper bound used", result.get("upper_bound_used", "—"))

        st.info(f"**Bet advice:** {result['bet_advice']}")
        st.write(f"**Reason:** {result['reason']}")

        # Session profit-lock overlay on machine decisions
        _sp2 = session_profit_status()
        if _sp2["status"] == "STOP_LOSS":
            st.error("Session stop-loss is active — do not start a new machine.")
        elif _sp2["status"] in ("LOCK_PROFIT", "TARGET_HIT"):
            st.warning("Profit-lock territory. Continue only on A-tier machines with reduced check-in.")
        elif _sp2["status"] == "AHEAD" and result["action"] == "JUDO JUMP":
            st.info("Already ahead — JJ only with a tight spin budget; protect the gain.")

        # Show the underlying profile so the player can trust / override
        with st.expander("🔍 Slot behaviour profile (what the engine actually saw)", expanded=False):
            prof = result.get("profile", {})
            if not prof.get("ok"):
                st.write(prof.get("reason", "No profile"))
            else:
                st.write(f"**Sample quality:** {prof.get('sample_quality')}  |  **Total logs:** {prof.get('total_logs')}  |  "
                         f"**Overall multi-hit rate:** {prof.get('overall_multi_hit_rate')}%  |  "
                         f"**Clustering score:** {prof.get('clustering_score')} (0=regular, 1=extreme clusters)")

                st.markdown("**Spin distributions by hit number**")
                rows = []
                for hn, info in sorted(prof.get("hit_numbers", {}).items()):
                    rows.append({
                        "Hit #": hn,
                        "Events": info.get("n_events"),
                        "Walk-offs (+)": info.get("n_censored"),
                        "Median": info.get("median"),
                        "P75": info.get("p75"),
                        "P85 (obs)": info.get("p85"),
                        "KM P85": info.get("km_p85"),
                        "Avg Mult": info.get("avg_mult"),
                        "Max Mult": info.get("max_mult"),
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                post = prof.get("post_win", {})
                if post:
                    st.markdown("**Post-win behaviour (JJ intelligence)**")
                    buckets = prof.get("mult_buckets", {})
                    st.caption(f"Small ≤ {buckets.get('q33')}×   |   Medium ≤ {buckets.get('q66')}×   |   Large > {buckets.get('q66')}×")
                    post_rows = []
                    for b in ["small", "medium", "large"]:
                        binfo = post.get(b, {})
                        post_rows.append({
                            "Win size": b.title(),
                            "First hits seen": binfo.get("n_first"),
                            "Re-hits": binfo.get("n_rehit"),
                            "Re-hit rate": f"{binfo.get('rehit_rate')}%" if binfo.get("rehit_rate") is not None else "—",
                            "Median spins to re-hit": binfo.get("median_spins_to_rehit"),
                            "P75 spins to re-hit": binfo.get("p75_spins_to_rehit"),
                        })
                    st.dataframe(pd.DataFrame(post_rows), use_container_width=True, hide_index=True)

                st.caption(
                    f"% of historical attempts still going after {int(spins_in)} spins: "
                    f"{result.get('pct_still_going', '—')}%"
                )

    else:
        st.markdown("---")
        st.info("Enter the current situation and press **Get Recommendation**.")

elif st.session_state.active_tab == "🃏 Gamble Analyzer":
    st.subheader("🃏 Gamble Analyzer")
    st.caption("Statistical (Markov) + AI suggestions side by side. Log either with one tap. Track both accuracies.")

    def _fmt_pct(v):
        return "n/a" if v is None else f"{v}%"

    # ---- Performance: Statistical (walk-forward) + AI (logged Source) ----
    with st.expander("📉 Accuracy — Statistical vs AI", expanded=True):
        bt = backtest_gamble_accuracy(window=50)
        perf = compute_ai_vs_stat_performance(window=50)

        st.markdown("**Statistical engine** (honest walk-forward)")
        _adapt = resolve_adaptive_fade(window=40)
        if bt is None:
            st.info("Need ~45+ logged rows for statistical backtest.")
        else:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Colour (all)", _fmt_pct(bt.get("overall_color_acc")), "vs 50%")
            c2.metric(f"Colour (last {bt['n_recent']})", _fmt_pct(bt.get("recent_color_acc")))
            c3.metric(f"FADE (last {bt['n_recent']})", _fmt_pct(bt.get("recent_fade_color_acc")))
            c4.metric("Suit (all)", _fmt_pct(bt["overall_suit_acc"]), "vs 25%")
            c5.metric("Adaptive now", _adapt["mode_label"].split(" ")[0])
            st.caption(_adapt["reason"])
            if _adapt["fade"]:
                st.warning("Adaptive: **FADE** — bet the opposite of the model colour.")
            else:
                st.success("Adaptive: **FOLLOW** — bet the model colour.")

        st.markdown("**AI suggestions** (from rows you logged with Source = AI)")
        if perf is None or (perf.get("ai_n_all") or 0) == 0:
            st.info("No AI-logged results yet. Use the AI card’s ✅ Correct button to start tracking.")
        else:
            a1, a2, a3 = st.columns(3)
            a1.metric("Suit (all AI)", _fmt_pct(perf["ai_suit_all"]), f"n={perf['ai_n_all']}")
            a2.metric(f"Suit (last {perf['window']} AI)", _fmt_pct(perf["ai_suit_recent"]), f"n={perf['ai_n_recent']}")
            a3.metric("Colour (all AI)", _fmt_pct(perf["ai_color_all"]))
            if perf.get("stat_n_all"):
                st.caption(
                    f"Head-to-head on logged rows — Stat suit { _fmt_pct(perf['stat_suit_all']) } "
                    f"(n={perf['stat_n_all']}) vs AI suit { _fmt_pct(perf['ai_suit_all']) } (n={perf['ai_n_all']})."
                )

    # ---- Card entry ----
    st.markdown("### Enter the 5 cards")
    cols = st.columns(4)
    for i, suit in enumerate(SUITS):
        with cols[i]:
            if st.button(f"{SUIT_EMOJI[suit]} {suit}", key=f"suit_btn_{suit}", use_container_width=True):
                if len(st.session_state.gamble_sequence) < 5:
                    st.session_state.gamble_sequence.append(suit)
                    st.session_state.ai_gamble_suggestion = None
                    st.rerun()

    seq = st.session_state.gamble_sequence

    if seq:
        html_parts = [suit_html(s) for s in seq]
        st.markdown("**Sequence:** " + " → ".join(html_parts) + f" &nbsp;({len(seq)}/5)", unsafe_allow_html=True)
        if st.button("↺ Clear sequence", key="clear_seq", use_container_width=True):
            st.session_state.gamble_sequence = []
            st.session_state.ai_gamble_suggestion = None
            st.rerun()
    else:
        st.info("Tap the four suits above to build the sequence.")

    if len(seq) == 5:
        _mode = st.session_state.get("gamble_fade_mode", "adaptive")
        _adapt = resolve_adaptive_fade(window=40)
        if _mode == "fade":
            fade_on = True
            _why = "Manual: always FADE"
        elif _mode == "follow":
            fade_on = False
            _why = "Manual: always FOLLOW"
        else:
            fade_on = bool(_adapt.get("fade"))
            _why = _adapt.get("reason", "Adaptive")
        st.session_state.fade_gamble = fade_on

        sug = get_gamble_suggestion(seq, fade_color=fade_on)
        # If strong pattern blocked fade, show FOLLOW even when adaptive wanted fade
        if sug.get("fade_blocked"):
            fade_on = False
            st.session_state.fade_gamble = False
            _why = f"Strong local pattern — FOLLOW (blocked fade). " + _why

        df_full = load_gamble_data()
        recent = df_full.tail(100) if len(df_full) > 100 else df_full
        extended = _build_extended_sequence(seq, recent)

        # ---- Statistical card (colour-first; adaptive fade) ----
        _roll = _adapt.get("recent_color_acc")
        _roll_s = f"{_roll}%" if _roll is not None else "n/a"
        mode_label = "Statistical (FADED – bet opposite colour)" if sug.get("faded") else "Statistical (FOLLOW model colour)"
        st.markdown(f"### {mode_label}")
        st.caption(
            f"Mode: **{_mode}** · Rolling model colour (last ~{_adapt.get('window', 40)}): **{_roll_s}** · {_why}"
        )
        st.markdown('<div class="sug-card stat">', unsafe_allow_html=True)
        st.markdown(
            f"**Colour to play** &nbsp; {color_html(sug['color'])}<br>"
            f"**Suit (optional)** &nbsp; {suit_html(sug['suit'])}",
            unsafe_allow_html=True
        )
        if sug.get("faded"):
            st.caption(
                f"Raw model colour was **{sug.get('raw_color_before_fade')}** — recommending opposite."
            )
        elif sug.get("fade_blocked"):
            st.caption("Strong pattern kept — not faded even if adaptive was cold.")
        conf = sug.get("confidence", "None")
        note = sug.get("note", "")
        match_len = sug.get("match_len", 0) or 0
        len_tag = f"{match_len}-card" if match_len else "base-rate"
        if conf == "Strong":
            st.success(f"**Strong {len_tag}** – {note}")
        elif conf == "Moderate":
            st.info(f"**Moderate {len_tag}** – {note}")
        elif conf == "Weak":
            st.warning(f"**Weak {len_tag}** – {note}")
        else:
            st.caption(f"No signal – {note}")
        if sug.get("outcomes"):
            breakdown = ", ".join(f"{s} ×{c}" for s, c in sorted(sug["outcomes"].items(), key=lambda x: -x[1]))
            st.caption(f"Seen {sug.get('match_count', 0)}× · Followed by: {breakdown}")
        st.markdown("</div>", unsafe_allow_html=True)

        if st.button("✅ Correct – Log Statistical", key="quick_correct", use_container_width=True, type="primary"):
            actual = sug["suit"]
            _pct = round(_pattern_color_rate(sug), 1)
            record = build_gamble_log_record(
                seq,
                sug["color"],
                sug["suit"],
                actual,
                "Statistical",
                faded=bool(sug.get("faded")),
                raw_model_color=sug.get("raw_color_before_fade") or sug.get("color") or "",
                confidence=sug.get("confidence") or "",
                match_len=sug.get("match_len") or "",
                match_count=sug.get("match_count") or "",
                pattern_pct=_pct,
                fade_mode=_mode,
                rolling_color_acc=_adapt.get("recent_color_acc") or "",
                note=sug.get("note") or "",
            )
            if append_gamble_record(record):
                st.session_state.gamble_sequence = seq[1:] + [actual]
                st.session_state.ai_gamble_suggestion = None
                st.success("Logged Statistical as Correct. Sequence rolled forward.")
                st.rerun()

        # ---- AI card ----
        st.markdown("### AI suggestion")
        if st.button("🤖 Ask AI for suggestion", key="ask_ai_gamble", use_container_width=True):
            with st.spinner("AI analysing patterns…"):
                ai_text, provider, parsed = get_ai_gamble_suggestion(seq, extended)
                st.session_state.ai_gamble_suggestion = (ai_text, provider, parsed)
                st.rerun()

        if st.session_state.ai_gamble_suggestion:
            # Support old 2-tuple and new 3-tuple
            packed = st.session_state.ai_gamble_suggestion
            if len(packed) == 3:
                ai_text, provider, parsed = packed
            else:
                ai_text, provider = packed
                parsed = parse_ai_gamble_response(ai_text)

            st.markdown('<div class="sug-card ai">', unsafe_allow_html=True)
            if parsed.get("ok"):
                st.markdown(
                    f"**Colour** &nbsp; {color_html(parsed['color'])}<br>"
                    f"**Suit** &nbsp;&nbsp;&nbsp;&nbsp; {suit_html(parsed['suit'])}",
                    unsafe_allow_html=True
                )
                if parsed.get("reason"):
                    st.caption(parsed["reason"])
                st.caption(f"Source: {provider}")
            else:
                st.warning("Could not parse Colour/Suit from AI reply. Raw response below.")
                st.markdown(ai_text)
                st.caption(f"Source: {provider}")
            st.markdown("</div>", unsafe_allow_html=True)

            if parsed.get("ok"):
                if st.button("✅ Correct – Log AI", key="quick_correct_ai", use_container_width=True, type="primary"):
                    actual = parsed["suit"]
                    record = build_gamble_log_record(
                        seq,
                        parsed["color"],
                        parsed["suit"],
                        actual,
                        "AI",
                        faded=False,
                        raw_model_color=parsed.get("color") or "",
                        confidence="AI",
                        match_len="",
                        match_count="",
                        pattern_pct="",
                        fade_mode=_mode,
                        rolling_color_acc=_adapt.get("recent_color_acc") or "",
                        note=(parsed.get("reason") or "")[:200],
                        provider=str(provider or ""),
                    )
                    if append_gamble_record(record):
                        st.session_state.gamble_sequence = seq[1:] + [actual]
                        st.session_state.ai_gamble_suggestion = None
                        st.success("Logged AI as Correct. Sequence rolled forward.")
                        st.rerun()

            with st.expander("Raw AI response"):
                st.markdown(ai_text)

        # ---- Manual log when both wrong ----
        st.markdown("### Both wrong? Log the real card")
        with st.form("log_gamble_result", clear_on_submit=False):
            actual = st.selectbox("Actual next card", options=SUITS, index=0, key="actual_select")
            # Which suggestion to attribute the miss to
            source_choice = st.radio(
                "Attribute this outcome to",
                options=["Statistical", "AI", "Both / Unknown"],
                horizontal=True,
                key="manual_source",
            )
            submitted = st.form_submit_button("💾 Log & roll forward", use_container_width=True)
            if submitted:
                if source_choice == "AI" and st.session_state.ai_gamble_suggestion:
                    packed = st.session_state.ai_gamble_suggestion
                    parsed_m = packed[2] if len(packed) == 3 else parse_ai_gamble_response(packed[0])
                    sug_color = parsed_m.get("color") or sug["color"]
                    sug_suit = parsed_m.get("suit") or sug["suit"]
                    src = "AI"
                    conf = "AI"
                    mlen = mc = pp = ""
                    faded_f = False
                    raw_c = sug_color
                else:
                    sug_color, sug_suit, src = sug["color"], sug["suit"], "Statistical"
                    conf = sug.get("confidence") or ""
                    mlen = sug.get("match_len") or ""
                    mc = sug.get("match_count") or ""
                    pp = round(_pattern_color_rate(sug), 1)
                    faded_f = bool(sug.get("faded"))
                    raw_c = sug.get("raw_color_before_fade") or sug.get("color") or ""
                record = build_gamble_log_record(
                    seq, sug_color, sug_suit, actual, src,
                    faded=faded_f,
                    raw_model_color=raw_c,
                    confidence=conf,
                    match_len=mlen,
                    match_count=mc,
                    pattern_pct=pp,
                    fade_mode=_mode,
                    rolling_color_acc=_adapt.get("recent_color_acc") or "",
                    note="",
                )
                if append_gamble_record(record):
                    st.session_state.gamble_sequence = seq[1:] + [actual]
                    st.session_state.ai_gamble_suggestion = None
                    st.success("Logged. Sequence rolled forward.")
                    st.rerun()

    # ---- Recent log ----
    st.markdown("---")
    st.markdown("### Recent log (last 12)")
    gdf = load_gamble_data()
    if not gdf.empty:
        show_cols = [
            c for c in [
                "Timestamp", "Sequence", "Suggested_Color", "Suggested_Suit",
                "Actual_Next", "Source", "Faded", "Confidence", "Pattern_Pct",
                "Fade_Mode", "Rolling_Color_Acc",
            ] if c in gdf.columns
        ]
        if "Source" not in gdf.columns:
            gdf = gdf.copy()
            gdf["Source"] = "Statistical"
            show_cols = [c for c in ["Timestamp", "Sequence", "Suggested_Suit", "Actual_Next", "Source"] if c in gdf.columns]
        recent_df = gdf[show_cols].tail(12).iloc[::-1].reset_index(drop=True)

        st.caption("Select mistakes to delete. Deletion updates the Google Sheet.")

        selected_timestamps = []
        for idx, row in recent_df.iterrows():
            ts = str(row.get("Timestamp", "")).strip()
            seq_str = str(row.get("Sequence", ""))
            sug_suit = str(row.get("Suggested_Suit", ""))
            act_next = str(row.get("Actual_Next", ""))
            src = str(row.get("Source", "Statistical"))

            col_chk, col_info = st.columns([0.1, 0.9])
            with col_chk:
                if st.checkbox("", key=f"del_chk_{ts}_{idx}", label_visibility="collapsed"):
                    selected_timestamps.append(ts)
            with col_info:
                st.markdown(
                    f"`{ts}` · **{seq_str}** → {sug_suit} · actual **{act_next}** · _{src}_"
                )

        if selected_timestamps:
            if st.button(f"🗑️ Delete {len(selected_timestamps)} selected", type="primary", key="delete_selected_gamble"):
                if delete_gamble_records(selected_timestamps):
                    st.success(f"Deleted {len(selected_timestamps)} record(s).")
                    st.rerun()
                else:
                    st.error("Delete failed.")
        else:
            st.caption("No records selected.")
    else:
        st.info("No records yet.")

elif st.session_state.active_tab == "📊 Today's Priority Board":
    st.subheader("Today's Priority Board")
    st.caption(
        "Per-slot ranking using KM-aware spin budgets, multi-hit rate, JJ tendency and post-big-win behaviour. "
        "AI-refined ranking still available below."
    )

    filtered_slots = []
    for s in st.session_state.slots_db:
        if s["slot"] in st.session_state.played_basket:
            continue
        rehit = s.get("rehit_metrics", {})
        first_total = rehit.get("first_hit_total", 0)
        # Need enough history to trust EV ranking
        if first_total >= 12:
            filtered_slots.append(s)

    current_display = filtered_slots[:st.session_state.display_limit]

    table_data = []
    for rank, item in enumerate(current_display, 1):
        b1 = item.get("budget_1st")
        b2 = item.get("budget_2nd")
        b3 = item.get("budget_3rd")
        multi = item.get("multi_hit_rate")
        table_data.append({
            "Rank": rank,
            "Family": item.get("family", "N/A"),
            "Slot": item.get("slot", "N/A"),
            "Tier": item.get("tier", 3),
            "Play Style": item.get("play_style", "—"),
            "JJ Tendency": item.get("jj_tendency", "—"),
            "Multi-Hit %": f"{multi:.0f}%" if multi is not None else "—",
            "Budget 1st": int(b1) if b1 is not None else "—",
            "Budget 2nd": int(b2) if b2 is not None else "—",
            "Budget 3rd": int(b3) if b3 is not None else "—",
            "Post-Big Note": item.get("post_big_note", "—"),
            "Sample": item.get("sample_quality", "—"),
            "Check-in $": get_recommended_checkin(
                b1 if b1 is not None else item.get("spin_1st"),
                item.get("slot"),
            ),
        })

    df_priority = pd.DataFrame(table_data)

    st.markdown(f"### Priority ranking for **{st.session_state.selected_day}**")
    if st.session_state.selected_day == "Friday":
        st.success("Friday target: +$500 then STOP. Fixed sequence from multi-Friday dry-runs. Maximus/Panda/Master Warrior/Shaolin out. No dig. Max 3 denoms ~$75 each.")
        rows = []
        for i, name in enumerate(FRIDAY_PLAY_ORDER, 1):
            pb = get_playbook(name)
            rows.append({
                "Seq": i,
                "Slot": name,
                "Check-in": pb.get("checkin", "—"),
                "Max spins": pb.get("max_spins", "—"),
                "After small": pb.get("after_small", "—"),
                "After med": pb.get("after_med", "—"),
                "After large": pb.get("after_large", "—"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption(
        "Changes when you change Filter Target Day in the sidebar. "
        "Tier 1 = primary · 2 = core · 3 = situational · 4 = only if up · 5 = skip. "
        "Day weighting is applied to the score."
    )
    if df_priority.empty:
        st.info("No slots with enough data.")
    else:
        csv_text = df_priority.to_csv(index=False, sep="\t")
        with st.expander("📋 Click here → Select All → Copy"):
            st.code(csv_text, language=None)

        st.dataframe(
            df_priority,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", width="small"),
                "Family": st.column_config.TextColumn("Family", width="medium"),
                "Slot": st.column_config.TextColumn("Slot", width="medium"),
                "Tier": st.column_config.NumberColumn("Tier", width="small"),
                "Play Style": st.column_config.TextColumn("Play Style", width="medium"),
                "JJ Tendency": st.column_config.TextColumn("JJ Tendency", width="small"),
                "Multi-Hit %": st.column_config.TextColumn("Multi-Hit %", width="small"),
                "Budget 1st": st.column_config.NumberColumn("Budget 1st", width="small"),
                "Budget 2nd": st.column_config.NumberColumn("Budget 2nd", width="small"),
                "Budget 3rd": st.column_config.NumberColumn("Budget 3rd", width="small"),
                "Post-Big Note": st.column_config.TextColumn("Post-Big Note", width="medium"),
                "Sample": st.column_config.TextColumn("Sample", width="small"),
                "Check-in $": st.column_config.NumberColumn("Check-in $", width="small"),
            }
        )

    if len(filtered_slots) > st.session_state.display_limit:
        if st.button("➕ Load 15 More Slots"):
            st.session_state.display_limit += 15
            st.rerun()

    # === AI Priority Ranking ===
    st.markdown("---")
    st.markdown("### AI-Refined Priority Ranking")
    st.caption("The AI reviews the top statistical candidates and produces its own ranked list (Gemini → Groq fallback).")

    if st.button("🤖 Ask AI for Priority Ranking", key="ask_ai_priority", type="primary"):
        with st.spinner("AI is analysing all data and ranking the best machines for today (Gemini → Groq)..."):
            ai_text, provider = get_ai_priority_ranking(
                st.session_state.slots_db,
                st.session_state.selected_day,
                st.session_state.played_basket
            )
            if ai_text:
                parsed = parse_ai_priority_list(ai_text, st.session_state.slots_db)
                st.session_state.ai_priority_result = (parsed, provider, ai_text)
            else:
                st.session_state.ai_priority_result = (None, provider, None)
            st.rerun()

    if st.session_state.ai_priority_result:
        parsed_list, provider, raw_text = st.session_state.ai_priority_result
        if parsed_list:
            st.success(f"AI ranking ready ({provider}) — showing {len(parsed_list)} machines")

            ai_table = []
            for rank, item in enumerate(parsed_list, 1):
                b1 = item.get("budget_1st") or item.get("spin_1st")
                b2 = item.get("budget_2nd") or item.get("spin_2nd")
                b3 = item.get("budget_3rd") or item.get("spin_3rd")
                multi = item.get("multi_hit_rate")
                ai_table.append({
                    "Rank": rank,
                    "Family": item.get("family", "N/A"),
                    "Slot": item.get("slot", "N/A"),
                    "Play Style": item.get("play_style", "—"),
                    "JJ Tendency": item.get("jj_tendency", "—"),
                    "Multi-Hit %": f"{multi:.0f}%" if multi is not None else "—",
                    "Budget 1st": int(b1) if b1 is not None else "—",
                    "Budget 2nd": int(b2) if b2 is not None else "—",
                    "Budget 3rd": int(b3) if b3 is not None else "—",
                    "Post-Big Note": item.get("post_big_note", "—"),
                    "Sample": item.get("sample_quality", "—"),
                })

            df_ai = pd.DataFrame(ai_table)

            csv_ai = df_ai.to_csv(index=False, sep="\t")
            with st.expander("📋 Click here → Select All → Copy (AI Ranking)"):
                st.code(csv_ai, language=None)

            st.dataframe(
                df_ai,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Rank": st.column_config.NumberColumn("Rank", width="small"),
                    "Family": st.column_config.TextColumn("Family", width="medium"),
                    "Slot": st.column_config.TextColumn("Slot", width="medium"),
                    "Play Style": st.column_config.TextColumn("Play Style", width="medium"),
                    "JJ Tendency": st.column_config.TextColumn("JJ Tendency", width="small"),
                    "Multi-Hit %": st.column_config.TextColumn("Multi-Hit %", width="small"),
                    "Budget 1st": st.column_config.NumberColumn("Budget 1st", width="small"),
                    "Budget 2nd": st.column_config.NumberColumn("Budget 2nd", width="small"),
                    "Budget 3rd": st.column_config.NumberColumn("Budget 3rd", width="small"),
                    "Post-Big Note": st.column_config.TextColumn("Post-Big Note", width="medium"),
                    "Sample": st.column_config.TextColumn("Sample", width="small"),
                }
            )
        else:
            st.warning(f"AI response could not be fully parsed ({provider}). Raw response:")
            st.code(raw_text or "No response")

elif st.session_state.active_tab == "📈 Overall Performance":
    st.subheader("📈 Overall Performance (All Historical Logs)")
    st.caption("Sorted by 1st Hits / Total (success rate). Higher hit rate ranks first.")

    overall_slots = []
    for fam, slots in SLOT_MASTER_LIST.items():
        for slot in slots:
            rehit = compute_slot_rehit_metrics(slot, fam, live_sheet_df)
            first_total = rehit.get("first_hit_total", 0)
            if first_total < 10:
                continue
            first_hits = rehit.get("first_hit_count", 0)
            success_rate = (first_hits / first_total) if first_total > 0 else 0.0
            avg_1st_mult = rehit.get("avg_first_multiplier", 0.0) or 0.0
            avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0) or 0.0
            multi_hit_count = rehit.get("multi_hit_count", 0) or 0

            sample_factor = min(1.0, first_total / 40.0)
            hit_quality = 0.65 + (0.35 * success_rate)
            robust_score = avg_1st_mult * sample_factor * hit_quality
            if multi_hit_count >= 4 and avg_2nd_mult >= 30:
                robust_score *= 1.12
            if slot in ["Maximus Money", "Minotaur’s Treasure", "Ragnar the Great", "Fire Mountain", "El Matador"]:
                robust_score *= 1.15
            if slot in ["Golden Empress", "New York Nights"]:
                robust_score *= 0.70

            overall_slots.append({
                "family": fam,
                "slot": slot,
                "calc_success_rate": success_rate,
                "avg_first_multiplier": avg_1st_mult,
                "robust_score": robust_score,
                "rehit_metrics": rehit
            })

    # Sort by 1st Hits / Total (success rate) descending
    sorted_overall = sorted(
        overall_slots,
        key=lambda x: (
            x["calc_success_rate"],
            x["rehit_metrics"].get("first_hit_count", 0),
            x["robust_score"]
        ),
        reverse=True
    )

    table_data_overall = []
    for rank, item in enumerate(sorted_overall, 1):
        rehit = item["rehit_metrics"]
        att2_pop = rehit.get("attempt2_population", 0)
        first_hits = rehit.get("first_hit_count", 0)
        first_total = rehit.get("first_hit_total", 0)
        avg_1st_mult = rehit.get("avg_first_multiplier", 0.0)
        avg_2nd_mult = rehit.get("avg_repeat_multiplier", 0.0)
        avg_spins = rehit.get("avg_first_spins", 0.0)
        success_pct = f"{round(item['calc_success_rate'] * 100, 1)}%"
        table_data_overall.append({
            "Rank": rank,
            "Slot Theme": item["slot"],
            "Family": item["family"],
            "Average Spin Count": f"{avg_spins}" if avg_spins > 0 else "N/A",
            "1st Hits/Total": f"{first_hits} / {first_total} ({success_pct})",
            "Avg 1st Mult": f"{avg_1st_mult}x" if avg_1st_mult > 0 else "N/A",
            "2nd Hits/Total": f"{rehit.get('multi_hit_count', 0)} / {att2_pop}",
            "Avg 2nd Mult": f"{avg_2nd_mult}x" if avg_2nd_mult > 0 else "N/A",
        })
    df_overall = pd.DataFrame(table_data_overall)
    if df_overall.empty:
        st.info("No slots with ≥ 10 total attempts found.")
    else:
        st.dataframe(df_overall, use_container_width=True, hide_index=True)

elif st.session_state.active_tab == "🤖 Interactive AI Agent":
    st.subheader("🤖 Slotpilot AI Assistant")
    with st.container():
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Active Day Focus", st.session_state.selected_day)
        m_col2.metric("Current Bankroll", f"${st.session_state.current_bankroll:.2f}")
        m_col3.metric("Target Goal", f"${st.session_state.session_target:.2f}")
        m_col4.metric("Played Today", f"{len(st.session_state.played_basket)} Machines")
    st.markdown("---")
    prompt_to_submit = None
    q_col1, q_col2, q_col3 = st.columns(3)
    with q_col1:
        if st.button("🏆 Top Priority Recommendation", use_container_width=True):
            prompt_to_submit = f"What are the top 3 best slots to play today ({st.session_state.selected_day})?"
    with q_col2:
        if st.button("🔥 High Repeat Multipliers", use_container_width=True):
            prompt_to_submit = "Which slots currently have the highest repeat-hit rate (>30%)?"
    with q_col3:
        if st.button("💵 Bankroll Strategy Check", use_container_width=True):
            prompt_to_submit = f"Given my current bankroll of ${st.session_state.current_bankroll:.2f}, guide my next play sequence."
    st.markdown("---")
    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    user_input = st.chat_input("Ask your Slotpilot AI...")
    if user_input:
        prompt_to_submit = user_input
    if prompt_to_submit:
        st.session_state.chat_messages.append({"role": "user", "content": prompt_to_submit})
        with st.chat_message("user"):
            st.markdown(prompt_to_submit)
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                response_text, provider = run_ai_agent(prompt_to_submit)
                st.caption(f"_Source: {provider}_")
                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})
        if st.session_state.get("pending_rerun"):
            st.session_state.pending_rerun = False
            st.rerun()

elif st.session_state.active_tab in ("🧺 Session & Basket", "🧺 Played Basket & Overrides"):
    st.subheader("🧺 Played Basket")
    if not st.session_state.played_basket:
        st.info("No machines marked as played yet today.")
    else:
        for slot in st.session_state.played_basket:
            col_p1, col_p2 = st.columns([3, 1])
            with col_p1:
                st.write(f"• **{slot}**")
            with col_p2:
                if st.button("Restore to Active", key=f"restore_{slot}"):
                    restore_slot(slot)
                    st.rerun()
        st.markdown("---")
        if st.button("🗑️ Clear Entire Played Basket"):
            st.session_state.played_basket = []
            persist_session_state()
            st.rerun()


elif st.session_state.active_tab == "📚 Learnings":
    st.subheader("📚 Learnings & knowledge base")
    st.caption("Claims checked against Session Log where possible. Not every opinion is true.")
    st.markdown("### Verified claims board")
    for c in VERIFIED_CLAIMS:
        st.markdown(f"**{c['claim']}**")
        st.caption(f"Source: {c['source']} · Verdict: {c['verdict']} · Confidence: {c['confidence']}")
        st.markdown(c['evidence'])
        st.markdown("---")

    for status, label in [
        ("Incorrect", "Fix these"),
        ("Correct", "Keep doing"),
        ("Observation", "Soft signals"),
    ]:
        items = [x for x in LEARNINGS if x.get("status") == status]
        if not items:
            continue
        st.markdown("#### " + label + " (" + status + ")")
        for x in items:
            topic = x.get("topic", "")
            date = x.get("date", "")
            conf = x.get("confidence", "")
            detail = x.get("detail", "")
            st.markdown(f"**{topic}** — {date} — confidence {conf}")
            st.markdown(detail)
            st.markdown("---")

    st.markdown("### Slot notes library")
    for slot, payload in SLOT_NOTES.items():
        with st.expander(f"{payload.get('family', '')} — {slot}"):
            for text, conf in payload.get("notes", []):
                st.markdown(f"- {text}")
                st.caption(f"Confidence: {conf}")
