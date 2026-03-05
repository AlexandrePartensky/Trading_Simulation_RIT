# RIT Trading Simulation

This repo contains a few scripts used during a Rotman Interactive Trader (RIT) simulation. The goal was to experiment with simple market making strategies while trading against other students during the session.

The strategies connect to the RIT REST API, read the order book, and place quotes while keeping inventory within limits.

## Files

- `sim_1_stable.py`  
  Basic market making strategy. Quotes around the mid price and adjusts the spread based on recent volatility. Includes simple inventory skew so the strategy naturally trades back toward flat.

- `sim_2_trend_regime.py`  
  Variant of the market maker that tries to adapt to different market conditions. It estimates short-term volatility and adjusts spreads and order sizes depending on whether the market looks more stable or directional.

- `control_board.ipynb`  
  Notebook used to monitor runs and visualize some metrics during testing.
