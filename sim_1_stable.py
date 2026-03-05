import time, requests

T1, T2 = 'CRZY_M', 'CRZY_A'
ORDER_CAP = 10_000
INV_LIMIT  = 25_000
SLEEP = 0.1

def headroom(inv, raw):
    buy_room  = INV_LIMIT - inv
    sell_room = INV_LIMIT + inv
    q = raw if raw < ORDER_CAP else ORDER_CAP
    if buy_room < q:  q = buy_room
    if sell_room < q: q = sell_room
    return q if q > 0 else 0

def get_tick(s):
    r = s.get(f'{HOST}/v1/case'); r.raise_for_status()
    return r.json()['tick']

def get_book(s, t):
    r = s.get(f'{HOST}/v1/securities/book', params={'ticker': t}); r.raise_for_status()
    j = r.json()
    a = j['asks'][0] if j['asks'] else None
    b = j['bids'][0] if j['bids'] else None
    ask_px = a['price'] if a else None; ask_q = int(a['quantity']) if a else 0
    bid_px = b['price'] if b else None; bid_q = int(b['quantity']) if b else 0
    return ask_px, ask_q, bid_px, bid_q

def post_mkt(s, t, side, qty):
    s.post(f'{HOST}/v1/orders',
           params={'ticker': t, 'type': 'MARKET', 'quantity': qty, 'action': side}
    ).raise_for_status()

def get_pos(s):
    r = s.get(f'{HOST}/v1/positions', params={'ticker': T1})
    if not r.ok: return 0
    d = r.json()
    if isinstance(d, list) and d: return int(d[0].get('position', 0))
    if isinstance(d, dict) and 'position' in d: return int(d['position'])
    return 0

def main():
    with requests.Session() as s:
        s.headers.update(API_KEY)
        s.trust_env = False
        adp = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0, pool_block=False)
        s.mount('http://', adp); s.mount('https://', adp)
        tick = get_tick(s)

        # Print immediately on first pass, then every 5s
        last_print = 0.0

        while 5 < tick < 999999:
            inv = get_pos(s)

            while True:
                a1, qA1, b1, qB1 = get_book(s, T1)
                a2, qA2, b2, qB2 = get_book(s, T2)
                if a1 is None or b2 is None or not (a1 < b2): break
                raw = qA1 if qA1 < qB2 else qB2
                qty = headroom(inv, raw)
                if qty <= 0: break
                post_mkt(s, T1, 'BUY',  qty)
                post_mkt(s, T2, 'SELL', qty)
                time.sleep(0.001)

            while True:
                a1, qA1, b1, qB1 = get_book(s, T1)
                a2, qA2, b2, qB2 = get_book(s, T2)
                if a2 is None or b1 is None or not (a2 < b1): break
                raw = qA2 if qA2 < qB1 else qB1
                qty = headroom(inv, raw)
                if qty <= 0: break
                post_mkt(s, T2, 'BUY',  qty)
                post_mkt(s, T1, 'SELL', qty)
                time.sleep(0.001)

            # print immediately, then every 5 seconds
            now = time.time()
            if now - last_print >= 5:
                a1, qA1, b1, qB1 = get_book(s, T1)
                a2, qA2, b2, qB2 = get_book(s, T2)
                print(f"[{time.strftime('%H:%M:%S')}] {T1} - Bid: {b1} ({qB1}), Ask: {a1} ({qA1}) | "
                      f"{T2} - Bid: {b2} ({qB2}), Ask: {a2} ({qA2})",
                      flush=True)
                last_print = now

            time.sleep(SLEEP)
            tick = get_tick(s)

if __name__ == '__main__':
    main()
