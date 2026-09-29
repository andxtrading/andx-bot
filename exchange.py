"""Exchange connectivity: market data (public, no keys needed) and two
brokers — PaperBroker (simulated fills, default) and LiveBroker (real orders
via ccxt on perpetual futures, so both longs and shorts work).
"""

import time

try:
    import ccxt  # optional: only for non-ANDX data fallbacks
except ImportError:
    ccxt = None

import andx

# The bot trades ANDX Global exclusively. (ccxt clients below remain as
# public market-DATA fallbacks only, never as trading venues.)
SUPPORTED_EXCHANGES = {
    "andx": "ANDX Global",
}

# Public-data fallbacks so paper trading works even if the chosen exchange
# geo-blocks public endpoints.
DATA_FALLBACKS = ["okx", "bybit", "krakenfutures", "kraken"]

TAKER_FEE = 0.00055   # 0.055% — typical perp taker fee, used in paper mode
PAPER_SLIPPAGE = 0.0003


def _make_client(exchange_id: str, creds: dict | None = None, testnet: bool = False):
    if ccxt is None:
        raise RuntimeError("ccxt is not installed; ANDX runs without it")
    cls = getattr(ccxt, exchange_id)
    cfg = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    if creds:
        cfg["apiKey"] = creds.get("api_key", "")
        cfg["secret"] = creds.get("api_secret", "")
        if creds.get("api_password"):
            cfg["password"] = creds["api_password"]
    client = cls(cfg)
    if testnet:
        client.set_sandbox_mode(True)
    return client


class MarketData:
    """Public OHLCV/ticker fetcher with cross-exchange fallback.

    ANDX has no candle endpoint, so when it is the venue, candles come from
    the fallback sources (major-pair prices track closely across venues)
    while last-price quotes come from ANDX itself."""

    def __init__(self, exchange_id: str):
        self.primary_id = exchange_id
        self._clients = {}
        self.active_source: str | None = None
        self._andx = andx.AndxClient() if exchange_id == "andx" else None

    def _client(self, exchange_id: str):
        if exchange_id not in self._clients:
            self._clients[exchange_id] = _make_client(exchange_id)
        return self._clients[exchange_id]

    def _try_each(self, fn):
        primary = [] if self.primary_id == "andx" else [self.primary_id]
        sources = primary + [x for x in DATA_FALLBACKS if x != self.primary_id]
        last_err = None
        for source in sources:
            try:
                result = fn(self._client(source))
                self.active_source = source
                return result
            except Exception as e:  # geo-block, downtime, unsupported symbol
                last_err = e
        raise RuntimeError(f"all market-data sources failed: {last_err}")

    def ohlcv(self, symbol: str, timeframe: str, limit: int = 300):
        # ANDX serves its own candles for the timeframes it supports
        if self._andx is not None and timeframe in ("15m", "1h", "1d"):
            try:
                bars = self._andx.price_bars(symbol, timeframe, limit)
                if len(bars) >= 60:
                    self.active_source = "andx"
                    return bars
            except Exception:
                pass
        return self._try_each(lambda c: c.fetch_ohlcv(symbol, timeframe, limit=limit))

    def last_price(self, symbol: str) -> float:
        if self._andx is not None:
            try:
                price = self._andx.last_price(symbol)
                self.active_source = "andx"
                return price
            except Exception:
                pass  # not on the spot ticker — try the margin service
            try:
                price = self._andx.margin_prices().get(andx.market_code(symbol))
                if price:
                    self.active_source = "andx"
                    return price
            except Exception:
                pass  # fall back to other venues' quotes
        ticker = self._try_each(lambda c: c.fetch_ticker(symbol))
        return float(ticker["last"])

    def spread_pct(self, symbol: str, margin: bool = False) -> float:
        """Bid/ask spread (fraction of mid) on the EXECUTION venue — pass
        margin=True when orders go to the ANDX margin service. Non-ANDX
        venues return a nominal 0.001 (they are liquid; execution happens
        on ANDX anyway)."""
        if self._andx is not None:
            try:
                if margin:
                    return self._andx.margin_spread_pct(symbol)
                return self._andx.spread_pct(symbol)
            except Exception:
                return 0.05  # unquotable right now — treat as too wide
        return 0.001


class Position:
    def __init__(self, symbol, side, qty, entry, stop, take_profit, strategy):
        self.symbol = symbol
        self.side = side            # +1 long, -1 short
        self.qty = qty
        self.entry = entry
        self.stop = stop
        self.take_profit = take_profit
        self.strategy = strategy
        self.opened_at = time.time()
        # Smart-exit state (see risk.manage_stop): R = the initial stop
        # distance; high_water = best price seen since entry.
        self.r_value = 0.0
        self.high_water: float | None = None

    def unrealized(self, price: float) -> float:
        return (price - self.entry) * self.qty * self.side

    def to_dict(self, price: float | None = None) -> dict:
        d = {
            "symbol": self.symbol,
            "side": "long" if self.side == 1 else "short",
            "qty": self.qty,
            "entry": self.entry,
            "stop": self.stop,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "opened_at": self.opened_at,
            "r_value": self.r_value,
            "high_water": self.high_water,
        }
        if price is not None:
            d["mark"] = price
            d["unrealized"] = self.unrealized(price)
        return d


class PaperBroker:
    """Simulated broker: fills at market price with slippage + taker fees.
    taker_fee is configurable so the sim can charge honest venue-like fees
    (ANDX spot taker is ~1%; the 0.055% default is a generic perp rate)."""

    mode = "paper"
    supports_short = True

    def __init__(self, start_balance: float = 10_000.0, taker_fee: float = TAKER_FEE):
        self.balance = start_balance
        self.taker_fee = taker_fee
        self.positions: dict[str, Position] = {}

    def equity(self, prices: dict[str, float]) -> float:
        return self.balance + sum(
            p.unrealized(prices.get(s, p.entry)) for s, p in self.positions.items()
        )

    def open_notional(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(s, p.entry) for s, p in self.positions.items())

    # demo book model: slippage grows with order size (a $50k one-shot
    # roughly doubles the base slip). Makes the sim honest about market
    # impact and makes order-slicing savings REAL within the model.
    IMPACT_SCALE_USD = 50_000.0

    def slip_for(self, notional: float) -> float:
        return PAPER_SLIPPAGE * (1 + max(0.0, notional) / self.IMPACT_SCALE_USD)

    def open(self, symbol, side, qty, price, stop, take_profit, strategy) -> Position:
        fill = price * (1 + self.slip_for(qty * price) * side)
        fee = fill * qty * self.taker_fee
        self.balance -= fee
        pos = Position(symbol, side, qty, fill, stop, take_profit, strategy)
        self.positions[symbol] = pos
        return pos

    def close(self, symbol, price) -> dict | None:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return None
        fill = price * (1 - PAPER_SLIPPAGE * pos.side)
        pnl = (fill - pos.entry) * pos.qty * pos.side
        fee = fill * pos.qty * self.taker_fee
        self.balance += pnl - fee
        return {"symbol": symbol, "side": pos.side, "qty": pos.qty,
                "entry": pos.entry, "exit": fill, "pnl": pnl - fee,
                "strategy": pos.strategy, "opened_at": pos.opened_at}


class LiveBroker(PaperBroker):
    """Real orders on perpetual futures via ccxt. Tracks its own position
    intents (entry/stop/strategy) while sending market orders to the exchange.
    Testnet mode uses the exchange's sandbox — same code path, fake money."""

    def __init__(self, exchange_id: str, creds: dict, testnet: bool = False,
                 start_balance: float = 0.0):
        super().__init__(start_balance)
        self.mode = "testnet" if testnet else "live"
        self.client = _make_client(exchange_id, creds, testnet)
        self.client.load_markets()
        self._sync_balance()

    def _sync_balance(self):
        bal = self.client.fetch_balance()
        usdt = bal.get("USDT", {}) or bal.get("total", {})
        total = usdt.get("total") if isinstance(usdt, dict) else None
        if total is None and isinstance(usdt, dict):
            total = usdt.get("free")
        if total is not None:
            self.balance = float(total)

    def open(self, symbol, side, qty, price, stop, take_profit, strategy) -> Position:
        amount = float(self.client.amount_to_precision(symbol, qty))
        order_side = "buy" if side == 1 else "sell"
        order = self.client.create_order(symbol, "market", order_side, amount)
        fill = float(order.get("average") or order.get("price") or price)
        pos = Position(symbol, side, amount, fill, stop, take_profit, strategy)
        self.positions[symbol] = pos
        return pos

    def close(self, symbol, price) -> dict | None:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return None
        order_side = "sell" if pos.side == 1 else "buy"
        order = self.client.create_order(
            symbol, "market", order_side, pos.qty, params={"reduceOnly": True}
        )
        fill = float(order.get("average") or order.get("price") or price)
        pnl = (fill - pos.entry) * pos.qty * pos.side
        self._sync_balance()
        return {"symbol": symbol, "side": pos.side, "qty": pos.qty,
                "entry": pos.entry, "exit": fill, "pnl": pnl,
                "strategy": pos.strategy, "opened_at": pos.opened_at}

    def equity(self, prices):
        # Exchange balance already reflects realized PnL; add unrealized.
        return self.balance + sum(
            p.unrealized(prices.get(s, p.entry)) for s, p in self.positions.items()
        )


class AndxBroker(PaperBroker):
    """Live spot trading on ANDX Global. Long/flat only (spot cannot short):
    long signals buy the coin, short/exit signals sell back to USDT.

    ANDX has no positions endpoint, so entries/stops are tracked bot-side,
    like the intents in LiveBroker; balances and fills are the exchange's."""

    supports_short = False
    mode = "live"

    def __init__(self, creds: dict):
        super().__init__(0.0)
        self.client = andx.AndxClient(
            api_key=creds.get("api_key", ""),
            api_secret=creds.get("api_secret", ""),
        )
        self._sync_balance()

    def _sync_balance(self):
        self.balance = self.client.balances().get("USDT", 0.0)

    @staticmethod
    def _order_fill(order: dict, fallback: float) -> tuple[float, float]:
        """Returns (fill_price, executed_qty) from a create_order response."""
        price = float(order.get("price") or 0) or fallback
        executed = float(order.get("executed_quantity") or 0)
        return price, executed

    def open(self, symbol, side, qty, price, stop, take_profit, strategy) -> Position:
        if side == -1:
            raise RuntimeError("ANDX spot cannot open shorts")
        qty = self.client.quantize(symbol, qty)
        if qty <= 0:
            raise RuntimeError("size below instrument minimum after precision rounding")
        order = self.client.create_market_order(symbol, "buy", qty)
        fill, _ = self._order_fill(order, price)
        # position = coins actually received (the ~1% taker fee comes out of them)
        pos = Position(symbol, 1, order["received"], fill, stop, take_profit, strategy)
        self.positions[symbol] = pos
        self._sync_balance()
        return pos

    def close(self, symbol, price) -> dict | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        # sell what the account actually holds (external moves, fee dust)
        base = self.client.base_currency(symbol)
        held = self.client.balances().get(base, 0.0)
        sell_qty = self.client.quantize(symbol, min(pos.qty, held))
        if sell_qty <= 0:
            # nothing to sell — position has no backing, drop the record
            self.positions.pop(symbol, None)
            raise RuntimeError(
                f"no {base} in account to sell (held {held:.8g}) — "
                "position record removed")
        pre_usdt = self.client.balances("total_balance").get("USDT", 0.0)
        order = self.client.create_market_order(symbol, "sell", sell_qty)
        self.positions.pop(symbol, None)  # only forget it once the sell filled
        fill, _ = self._order_fill(order, price)
        # True PnL = USDT that actually landed minus what the coins cost.
        # The gross fill price overstates it: ANDX's ~1% taker fee comes out
        # of the proceeds, and pretending otherwise inflates the win rate.
        proceeds = 0.0
        try:
            proceeds = self.client.balances("total_balance").get("USDT", 0.0) - pre_usdt
        except Exception:
            pass
        if proceeds <= 0:  # balance query hiccup — estimate net of ~1% fee
            proceeds = fill * sell_qty * 0.99
        exit_eff = proceeds / sell_qty
        pnl = proceeds - pos.entry * sell_qty
        self._sync_balance()
        return {"symbol": symbol, "side": pos.side, "qty": sell_qty,
                "entry": pos.entry, "exit": exit_eff, "pnl": pnl,
                "strategy": pos.strategy, "opened_at": pos.opened_at}

    def equity(self, prices):
        return self.balance + sum(
            p.qty * prices.get(s, p.entry) for s, p in self.positions.items()
        )


def _symbol_from_code(code: str) -> str:
    """'XRPUSDT' -> 'XRP/USDT'"""
    for quote in ("USDT", "USDC", "USD"):
        if code.endswith(quote) and len(code) > len(quote):
            return f"{code[:-len(quote)]}/{quote}"
    return code


class AndxMarginBroker(PaperBroker):
    """Live ANDX derivatives: real longs AND shorts with leverage; stop-loss
    and take-profit are enforced by the exchange. The exchange's open-
    positions list is the source of truth — sync() reconciles every tick and
    reports positions the exchange closed on its own (SL/TP hits)."""

    supports_short = True
    mode = "live"

    def __init__(self, creds: dict, leverage: int = 2):
        super().__init__(0.0)
        self.client = andx.AndxClient(
            api_key=creds.get("api_key", ""),
            api_secret=creds.get("api_secret", ""),
        )
        self.leverage = max(1, int(leverage))
        self._sync_balance()
        self.sync()

    def _sync_balance(self):
        # total (not just free) USDT so margin collateral stays part of
        # equity; the API returns one row per wallet, so sum them
        data = self.client._gql(
            "{ accounts_balances { currency_id total_balance } }", auth=True)
        self.balance = sum(
            float(b["total_balance"] or 0) for b in data["accounts_balances"]
            if b["currency_id"] == "USDT")

    def _position_from_row(self, row: dict) -> Position:
        symbol = _symbol_from_code(row["instrument_id"])
        side = 1 if row["side"] == "buy" else -1
        pos = Position(symbol, side, float(row["amount"]),
                       float(row["entry_price"]),
                       float(row.get("stop_loss") or 0),
                       row.get("take_profit"), "auto")
        ts = row.get("start_ts_timestamp")
        if ts:
            ts = float(ts)
            pos.opened_at = ts / 1000 if ts > 1e12 else ts
        pos.margin_id = row["margin_position_id"]
        pos.leverage = int(row.get("leverage") or self.leverage)
        pos.server_pnl = float(row.get("pnl") or 0)
        return pos

    def sync(self) -> list[dict]:
        """Reconcile with the exchange; returns trade dicts for positions the
        exchange closed since the last sync (stop-loss/take-profit)."""
        server: dict[str, Position] = {}
        for row in self.client.margin_positions():
            pos = self._position_from_row(row)
            old = self.positions.get(pos.symbol)
            if old is not None and getattr(old, "margin_id", None) == pos.margin_id:
                pos.strategy = old.strategy  # keep which strategy opened it
                # smart-exit state lives bot-side; the every-tick server
                # resync must not wipe it
                pos.r_value = getattr(old, "r_value", 0.0)
                pos.high_water = getattr(old, "high_water", None)
                if old.stop and not float(row.get("stop_loss") or 0):
                    pos.stop = old.stop
            server[pos.symbol] = pos
        closed = []
        for symbol, old in self.positions.items():
            if symbol in server:
                continue
            pnl = getattr(old, "server_pnl", 0.0)
            exit_price, reason, confirmed = old.entry, "exchange close", False
            try:
                for c in self.client.closed_margin_positions(20):
                    if c["margin_position_id"] == getattr(old, "margin_id", None):
                        pnl = float(c.get("pnl") or pnl)
                        reason = str(c.get("close_reason") or reason)
                        exit_price = float(
                            c.get("end_bid_price" if old.side == 1 else "end_ask_price")
                            or old.entry)
                        confirmed = True
                        break
            except Exception:
                pass
            if not confirmed:
                # Missing from the open list but ALSO absent from closed
                # history: more likely a flaky/empty API response than a real
                # close. Keep the position one more tick before believing it —
                # a single empty response must not book a phantom trade and
                # invite a duplicate re-entry.
                old._sync_miss = getattr(old, "_sync_miss", 0) + 1
                if old._sync_miss <= 1:
                    server[symbol] = old
                    continue
                reason = "exchange close (unconfirmed)"
            closed.append({"symbol": symbol, "side": old.side, "qty": old.qty,
                           "entry": old.entry, "exit": exit_price, "pnl": pnl,
                           "strategy": old.strategy, "opened_at": old.opened_at,
                           "reason": reason})
        self.positions = server
        if closed:
            self._sync_balance()
        return closed

    def open(self, symbol, side, qty, price, stop, take_profit, strategy) -> Position:
        spec = self.client.margin_instrument_spec(symbol)
        lev = self.leverage
        lev = max(lev, int(spec.get("min_leverage") or 1))
        lev = min(lev, int(spec.get("max_leverage") or lev))
        row = self.client.open_margin(symbol, side, qty, lev,
                                      stop_loss=stop, take_profit=take_profit)
        pos = self._position_from_row(row)
        pos.strategy = strategy
        if not pos.stop and stop:
            pos.stop = stop
        self.positions[pos.symbol] = pos
        self._sync_balance()
        return pos

    def close(self, symbol, price) -> dict | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        self.client.close_margin(pos.margin_id)
        self.positions.pop(symbol, None)
        pnl = getattr(pos, "server_pnl", (price - pos.entry) * pos.qty * pos.side)
        exit_price = price
        try:
            for c in self.client.closed_margin_positions(10):
                if c["margin_position_id"] == pos.margin_id:
                    pnl = float(c.get("pnl") or pnl)
                    exit_price = float(
                        c.get("end_bid_price" if pos.side == 1 else "end_ask_price")
                        or price)
                    break
        except Exception:
            pass
        self._sync_balance()
        return {"symbol": symbol, "side": pos.side, "qty": pos.qty,
                "entry": pos.entry, "exit": exit_price, "pnl": pnl,
                "strategy": pos.strategy, "opened_at": pos.opened_at}

    def push_stop(self, symbol: str, stop: float):
        pos = self.positions.get(symbol)
        if pos and getattr(pos, "margin_id", None):
            self.client.update_margin(pos.margin_id,
                                      getattr(pos, "leverage", self.leverage),
                                      stop_loss=stop, take_profit=pos.take_profit)
            pos.stop = stop

    def equity(self, prices):
        # balance misses what the exchange moved into the margin wallet as
        # collateral, so add each open position's collateral (notional/leverage)
        # plus its live pnl
        total = self.balance
        for s, p in self.positions.items():
            total += (p.qty * p.entry) / max(1, getattr(p, "leverage", self.leverage))
            pnl = getattr(p, "server_pnl", None)
            total += pnl if pnl is not None else p.unrealized(prices.get(s, p.entry))
        return total


def check_credentials(exchange_id: str, creds: dict, testnet: bool = False) -> dict:
    """Validate keys by fetching the account balance. Returns {ok, detail}."""
    if exchange_id == "andx":
        return andx.check_credentials(creds)
    try:
        client = _make_client(exchange_id, creds, testnet)
        bal = client.fetch_balance()
        usdt = bal.get("USDT", {})
        total = usdt.get("total") if isinstance(usdt, dict) else None
        return {"ok": True, "detail": f"Connected. USDT balance: {total if total is not None else 'n/a'}"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
