import time, requests, math

T       = "ALGO"            # ALGO2 is single-stock
ORDER_CAP = 5_000           # per-order limit per case brief
INV_LIMIT = 25_000          # gross/net limit
SLEEP    = 0.10             # throttle


from collections import deque
import math

TICK = 0.01                 # price tick
VOL_WIN = 60                # how many mids to keep (≈ last 60 loops)
LAMBDA = 0.2                # EWMA smoothing for volatility
ZVOL = 2.0                  # how many vol units to target for half-spread
SPREAD_MIN = 0.02           # never tighter than 2 ticks (protects from flicker)
SPREAD_MAX = 0.50           # safety cap
ALPHA_TOB = 0.5             # blend toward top-of-book width (0..1)
SKEW_K = 0.75               # skew strength (0 = none, 1 = full spread shift at INV_LIMIT)

mids_window = deque(maxlen=VOL_WIN)
ewma_abs_ret = 0.0

def round_down(x, tick=TICK):
    return math.floor(x / tick) * tick

def round_up(x, tick=TICK):
    return math.ceil(x / tick) * tick

def update_vol(mid):
    """EWMA of absolute mid-price returns (in $), returned as a 1-step vol proxy."""
    global ewma_abs_ret
    if mids_window:
        ar = abs(mid - mids_window[-1])
        ewma_abs_ret = LAMBDA * ar + (1 - LAMBDA) * ewma_abs_ret
    mids_window.append(mid)
    return ewma_abs_ret

def dynamic_spread(a1, b1, inv):
    """
    Choose a baseline spread from:
      - vol:      2 * ZVOL * EWMA(|Δmid|)  (full spread)
      - toc:      current top-of-book spread (a1-b1), if both exist
      - floor:    SPREAD_MIN; cap at SPREAD_MAX
    Then inventory-skew: shift quotes by SKEW_K * (inv/INV_LIMIT) * spread
    """
    # mid & top-of-book width
    if a1 is None or b1 is None:
        return None, None, None  # insufficient book

    tob = max(a1 - b1, TICK)
    mid = 0.5 * (a1 + b1)

    # volatility proxy
    vol = update_vol(mid)                        # in $
    vol_spread = max(2 * ZVOL * vol, SPREAD_MIN)
    # blend with top-of-book to stay competitive but not cross
    base_spread = max(SPREAD_MIN, min(SPREAD_MAX, (1 - ALPHA_TOB) * vol_spread + ALPHA_TOB * tob))

    # inventory skew: positive inv (long) shifts both quotes DOWN to encourage selling;
    # negative inv (short) shifts UP to encourage buying.
    skew = SKEW_K * (inv / INV_LIMIT) * base_spread

    # build quotes around mid, apply skew, and respect tick + don’t cross touch
    raw_bid = mid - 0.5 * base_spread - skew
    raw_ask = mid + 0.5 * base_spread - skew

    bid_px = min(round_down(raw_bid, TICK), b1)     # never price through best bid
    ask_px = max(round_up(raw_ask, TICK), a1)       # never price through best ask

    # safety: if rounding/caps created a cross, widen one tick
    if bid_px >= ask_px:
        bid_px = min(bid_px, mid - TICK)
        ask_px = max(ask_px, mid + TICK)

    return bid_px, ask_px, base_spread

def size_with_inventory(inv, q_touch):
    """
    Quantity skew: quote smaller size on the side that increases your risk,
    larger size on the side that reduces it. Keep within ORDER_CAP and headroom.
    """
    base_qty = max(100, min(q_touch, ORDER_CAP))  # seed size from touch; min lot 100
    bias = min(0.6, abs(inv) / INV_LIMIT)         # up to 60% tilt
    if inv > 0:    # long: prefer to sell more, buy less
        bid_qty = int(base_qty * (1.0 - bias))
        ask_qty = int(base_qty * (1.0 + bias))
    elif inv < 0:  # short: prefer to buy more, sell less
        bid_qty = int(base_qty * (1.0 + bias))
        ask_qty = int(base_qty * (1.0 - bias))
    else:
        bid_qty = ask_qty = int(base_qty)
    return max(bid_qty, 0), max(ask_qty, 0)



# ===== API helpers (adjust endpoints/params to your server) =====
def get_tick(s):
    r = s.get(f"{HOST}/v1/case"); r.raise_for_status()
    return r.json()['tick']

def get_book(s, t):
    r = s.get(f"{HOST}/v1/securities/book", params={"ticker": t}); r.raise_for_status()
    j = r.json()
    a = j['asks'][0] if j['asks'] else None
    b = j['bids'][0] if j['bids'] else None
    ask_px = a['price'] if a else None; ask_q = int(a['quantity']) if a else 0
    bid_px = b['price'] if b else None; bid_q = int(b['quantity']) if b else 0
    return ask_px, ask_q, bid_px, bid_q

def get_pos(s, t):
    r = s.get(f"{HOST}/v1/positions", params={"ticker": t})
    if not r.ok: return 0
    d = r.json()
    if isinstance(d, list) and d: return int(d[0].get('position', 0))
    if isinstance(d, dict) and 'position' in d: return int(d['position'])
    return 0

def list_open_orders(s, t):
    r = s.get(f"{HOST}/v1/orders", params={"ticker": t}); r.raise_for_status()
    return [o for o in r.json() if o.get("status") in {"OPEN","ACCEPTED","PARTIAL"}]

def cancel_order(s, order_id):
    r = s.delete(f"{HOST}/v1/orders/{order_id}"); r.raise_for_status()

def cancel_all(s, t):
    for o in list_open_orders(s, t):
        cancel_order(s, o["order_id"])

def post_limit(s, t, side, qty, px):
    r = s.post(f"{HOST}/v1/orders",
               params={"ticker": t, "type": "LIMIT", "quantity": qty, "price": px, "action": side})
    r.raise_for_status()
    return r.json().get("order_id")

# ===== sizing & quoting =====
def clamp_qty(inv, desired):
    # respect per-order cap and position limit after fill
    desired = min(desired, ORDER_CAP)
    if desired <= 0: return 0
    buy_room  = INV_LIMIT - inv
    sell_room = INV_LIMIT + inv
    return max(0, min(desired, buy_room, sell_room))

def quote_prices(ask_px, bid_px, inv):
    # Use mid & current spread; inventory skew pulls you to flat
    if ask_px is None or bid_px is None: return None, None
    mid   = 0.5 * (ask_px + bid_px)
    spr   = max(ask_px - bid_px, 0.01)   # at least 1 tick
    base  = spr * 0.5
    skew  = (inv / INV_LIMIT) * spr      # skew fraction of spread
    bid_q = round(mid - base - skew, 2)
    ask_q = round(mid + base - skew, 2)
    # keep quotes near top of book without crossing
    bid_q = min(bid_q, bid_px)           # don’t cross
    ask_q = max(ask_q, ask_px)
    return bid_q, ask_q

def main():
    with requests.Session() as s:
        s.headers.update(API_KEY)
        s.trust_env = False
        adp = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0, pool_block=False)
        s.mount('http://', adp); s.mount('https://', adp)

        last_refresh = 0.0
        while True:
            tick = get_tick(s)
            if not (0 <= tick <= 999999):
                break
            if tick >= 300:  # end of case (~5 minutes)
                cancel_all(s, T)
                break

            inv = get_pos(s, T)
            a1, qA1, b1, qB1 = get_book(s, T)

            # Compute dynamic, inventory-skewed quotes
            bid_px, ask_px, eff_spread = dynamic_spread(a1, b1, inv)
            if bid_px is None:
                time.sleep(SLEEP)
                continue

            # Decide sizes (skewed toward reducing inventory)
            touch_hint = min(qA1 or ORDER_CAP, qB1 or ORDER_CAP, ORDER_CAP)
            bid_qty_raw, ask_qty_raw = size_with_inventory(inv, touch_hint)
            bid_qty = clamp_qty(inv, bid_qty_raw)
            ask_qty = clamp_qty(inv, ask_qty_raw)

            # Maintain exactly one bid and one ask resting
            open_orders = list_open_orders(s, T)
            bids = [o for o in open_orders if o["action"] == "BUY"]
            asks = [o for o in open_orders if o["action"] == "SELL"]

            now = time.time()
            need_refresh = (len(bids) != 1 or len(asks) != 1)
            price_drift  = (not need_refresh) and (
                abs(bids[0]["price"] - bid_px) >= 0.02 or
                abs(asks[0]["price"] - ask_px) >= 0.02
            )

            if need_refresh or price_drift or (now - last_refresh) > 1.5:
                cancel_all(s, T)
                if bid_qty > 0:
                    post_limit(s, T, "BUY",  bid_qty, bid_px)   # <-- quoting call
                if ask_qty > 0:
                    post_limit(s, T, "SELL", ask_qty, ask_px)   # <-- quoting call
                last_refresh = now

            time.sleep(SLEEP)

if __name__ == "__main__":
    main()

