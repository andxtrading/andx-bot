"""coach.py — the trader-facing intelligence: your rules vs you, the
assistant's own scorecard, conviction calibration, and the daily One Thing.

Pure computation over the real journal. Every function returns plain dicts
with honesty flags — small samples say so, and nothing is ever invented.
"""

from datetime import datetime

REVENGE_WINDOW_S = 30 * 60
LATE_HOURS = (0, 5)  # local, inclusive start / exclusive end


def _bucket(rows):
    if not rows:
        return {"trades": 0, "pnl": 0.0, "win_rate_pct": None}
    wins = sum(1 for t in rows if t["pnl"] > 0)
    return {"trades": len(rows),
            "pnl": round(sum(t["pnl"] for t in rows), 2),
            "win_rate_pct": round(wins / len(rows) * 100)}


def rules_vs_you(trades: list, events: list, fills: list) -> dict:
    """Where the user's own discipline gap lives — split by how trades
    ENDED, when they happened, and what the stop-move journal shows."""
    n = len(trades)
    if not n:
        return {"trades": 0,
                "note": "no closed trades in the window yet — nothing to judge"}
    planned, discretionary, engine_calls = [], [], []
    for t in trades:
        r = (t.get("reason") or "").lower()
        if r in ("stop loss", "take profit", "trailing stop"):
            planned.append(t)
        elif r in ("manual close",):
            discretionary.append(t)
        else:  # signal exit, turnover, flatten, kill switch…
            engine_calls.append(t)
    late = [t for t in trades
            if LATE_HOURS[0] <= datetime.fromtimestamp(t["ts"]).hour < LATE_HOURS[1]]
    # revenge candidates: a BUY fill within 30 min after a losing close
    losing_closes = [t["ts"] for t in trades if t["pnl"] < 0]
    revenge_fills = []
    for f in fills:
        if f["side"] != "buy":
            continue
        if any(0 < f["ts"] - lt <= REVENGE_WINDOW_S for lt in losing_closes):
            revenge_fills.append(f)
    loosened = [e for e in events if e["kind"] == "loosened_stop"]
    tightened = [e for e in events if e["kind"] == "tightened_stop"]
    return {
        "trades": n,
        "exits_by_plan": _bucket(planned),
        "exits_by_your_hand": _bucket(discretionary),
        "exits_by_engine_signal": _bucket(engine_calls),
        "late_night_trades_0to5am_local": _bucket(late),
        "revenge_entries_within_30min_of_a_loss": len(revenge_fills),
        "stops_loosened": len(loosened),
        "stops_tightened": len(tightened),
        "stop_move_log_started": "stop moves are journaled from Sep 8, 2026 — "
                                 "the never-moved-a-stop counterfactual gets "
                                 "exact as this log grows",
        "small_sample_warning": n < 20,
        "note": "pnl includes fees; hours are LOCAL time; revenge detection "
                "is approximate (buy within 30min of any losing close)",
    }


def track_record(trades: list) -> dict:
    """The assistant's own public scorecard vs the user's manual trades.
    strategy == 'manual' means the user commanded it; everything else is
    the engine's own call."""
    mine = [t for t in trades if (t.get("strategy") or "") != "manual"]
    yours = [t for t in trades if (t.get("strategy") or "") == "manual"]

    def card(rows):
        b = _bucket(rows)
        if not rows:
            return b
        wins = [t["pnl"] for t in rows if t["pnl"] > 0]
        losses = [t["pnl"] for t in rows if t["pnl"] <= 0]
        b["avg_win"] = round(sum(wins) / len(wins), 2) if wins else None
        b["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else None
        gp, gl = sum(wins), abs(sum(losses))
        b["profit_factor"] = round(gp / gl, 2) if gl > 0 else None
        b["best"] = round(max(t["pnl"] for t in rows), 2)
        b["worst"] = round(min(t["pnl"] for t in rows), 2)
        return b

    return {
        "my_trades_the_engines_calls": card(mine),
        "your_manual_trades": card(yours),
        "honesty": "wins AND losses, fees included — judge me on this, "
                   "not my confidence",
        "small_sample_warning": (len(mine) + len(yours)) < 20,
    }


def calibration(convictions: list, trades: list) -> dict:
    """When you say 'sure', how right are you? Joins each conviction to the
    first closed trade on that symbol AFTER it (within 72h)."""
    if not convictions:
        return {"convictions_logged": 0,
                "note": "say how confident you feel when you place a trade "
                        "('I'm sure' / 'worth a shot') and I'll start "
                        "measuring your words against your results"}
    joined = {}
    for c in convictions:
        match = None
        for t in trades:
            if (t["symbol"] == c["symbol"] and 0 < t["ts"] - c["ts"] <= 72 * 3600):
                match = t
                break
        if match:
            joined.setdefault(c["klass"], []).append(match)
    out = {k: _bucket(v) for k, v in joined.items()}
    resolved = sum(b["trades"] for b in out.values())
    return {
        "convictions_logged": len(convictions),
        "resolved_against_closed_trades": resolved,
        "by_confidence": out,
        "small_sample_warning": resolved < 8,
        "note": "a conviction resolves when its trade closes; under ~8 "
                "resolved this is a curiosity, not a verdict",
    }


def one_thing(equity, balance, positions, trades, events, convictions,
              day_pnl) -> dict:
    """Exactly ONE insight — the highest-value true thing right now.
    Scarcity is the feature: if nothing clears the bar, say so proudly."""
    cands = []
    n = len(trades)
    if equity and balance is not None and equity > 0:
        idle = balance / equity
        if idle > 0.9 and not positions and n >= 3:
            cands.append((40, f"You're {idle*100:.0f}% in cash with nothing "
                              "working. Fine as a choice — worth knowing "
                              "it's the position you're in."))
    if n >= 10:
        by_hour = {}
        for t in trades:
            by_hour.setdefault(datetime.fromtimestamp(t["ts"]).hour, []).append(t)
        worst = min(by_hour.items(),
                    key=lambda kv: sum(x["pnl"] for x in kv[1]))
        wp = sum(x["pnl"] for x in worst[1])
        if wp < 0 and len(worst[1]) >= 3:
            cands.append((70, f"Your most expensive hour is {worst[0]:02d}:00 "
                              f"local — {len(worst[1])} trades, "
                              f"${wp:,.0f}. Worth a look before you trade "
                              "in that window again."))
    loosened = [e for e in events if e["kind"] == "loosened_stop"]
    if len(loosened) >= 2:
        cands.append((80, f"You've loosened stops {len(loosened)} times "
                          "recently. That's the single habit that turns "
                          "small losses into big ones — want me to make "
                          "your stops binding?"))
    mine = [t for t in trades if (t.get("strategy") or "") != "manual"]
    yours = [t for t in trades if (t.get("strategy") or "") == "manual"]
    if len(mine) >= 5 and len(yours) >= 5:
        gap = sum(t["pnl"] for t in mine) - sum(t["pnl"] for t in yours)
        who = "my calls are ahead of your manual trades" if gap > 0 else \
              "your manual trades are beating my calls"
        cands.append((60, f"Scorecard check: {who} by ${abs(gap):,.0f} in "
                          "this window. Ask for the full track record "
                          "any time."))
    if day_pnl is not None and equity and abs(day_pnl) > equity * 0.03:
        word = "up" if day_pnl > 0 else "down"
        cands.append((90, f"Today is a big one — {word} ${abs(day_pnl):,.0f} "
                          f"({abs(day_pnl)/equity*100:.1f}%). Big days are "
                          "when rules matter most; nothing needs doing, "
                          "just eyes open."))
    if not cands:
        return {"one_thing": None,
                "say": "nothing clears my bar today — that's a good sign, "
                       "not a lazy one. I only bring you what matters."}
    cands.sort(key=lambda c: -c[0])
    return {"one_thing": cands[0][1],
            "others_held_back": len(cands) - 1,
            "law": "one per day, maximum — scarcity is why it's worth "
                   "listening to"}
