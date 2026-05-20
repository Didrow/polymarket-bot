"""
reset_stats.py — Polymarket Weather Bot
Utility script to clean up historical stats in JSONBin and local bot_state.json
while keeping the active open positions intact.
"""

import os
import json
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load env variables
load_dotenv()

import config
from safeguards import SafeguardManager, BotState, _jsonbin_load, _jsonbin_save

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def reset_statistics():
    logger.info("Starting stats cleanup...")
    
    # Initialize safeguard manager to read current state
    manager = SafeguardManager()
    current_state = manager.state
    
    active_positions_count = len(current_state.open_positions or {})
    logger.info(f"Current state loaded:")
    logger.info(f"  - Total Trades: {current_state.total_trades}")
    logger.info(f"  - Winning Trades: {current_state.winning_trades}")
    logger.info(f"  - Losing Trades: {current_state.losing_trades}")
    logger.info(f"  - Total PnL: ${current_state.total_pnl:.2f}")
    logger.info(f"  - Active Open Positions: {active_positions_count}")
    
    # We will reset statistics but keep open positions
    logger.info("Resetting historical statistics...")
    
    new_state = BotState(
        initial_capital=config.INITIAL_CAPITAL,
        current_capital=config.INITIAL_CAPITAL,
        peak_capital=config.INITIAL_CAPITAL,
        total_trades=active_positions_count, # The open positions count as opened trades
        winning_trades=0,
        losing_trades=0,
        total_pnl=0.0,
        is_halted=False,
        halt_reason="",
        start_time=datetime.now(timezone.utc).isoformat(),
        last_update=datetime.now(timezone.utc).isoformat(),
        open_positions=current_state.open_positions
    )
    
    # Assign the new state to the manager
    manager.state = new_state
    
    # Save the cleaned state to local and JSONBin
    manager.save_state()
    logger.info("✅ Statistics successfully reset!")
    logger.info("New state saved:")
    logger.info(f"  - Total Trades: {manager.state.total_trades} (representing active positions)")
    logger.info(f"  - Winning Trades: {manager.state.winning_trades}")
    logger.info(f"  - Losing Trades: {manager.state.losing_trades}")
    logger.info(f"  - Total PnL: ${manager.state.total_pnl:.2f}")
    logger.info(f"  - Active Open Positions kept: {len(manager.state.open_positions or {})}")

if __name__ == "__main__":
    reset_statistics()
