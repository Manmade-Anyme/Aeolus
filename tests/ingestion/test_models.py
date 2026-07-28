from datetime import date, datetime, timezone

from aeolus.ingestion.models import (
    DepthLevel,
    Greeks,
    IngestionSnapshot,
    MarketDepth,
    OptionStrike,
)


def _greeks() -> Greeks:
    return Greeks(delta=0.5, gamma=0.01, theta=-2.0, vega=1.2)


def test_snapshot_all_fields_present():
    snapshot = IngestionSnapshot(
        ts=datetime.now(timezone.utc),
        spot_ltp=24500.0,
        futures_ltp=24550.0,
        futures_basis=50.0,
        depth=MarketDepth(
            bid_levels=[DepthLevel(price=24499.5, quantity=100)],
            ask_levels=[DepthLevel(price=24500.5, quantity=120)],
        ),
        option_chain=[
            OptionStrike(
                strike=24500,
                call_oi=1000,
                put_oi=1200,
                call_iv=14.2,
                put_iv=14.5,
                call_greeks=_greeks(),
                put_greeks=_greeks(),
            )
        ],
        india_vix=13.2,
        gift_nifty=None,
        volume=1_500_000,
        total_buy_quantity=800_000,
        total_sell_quantity=700_000,
        day_high=24600.0,
        day_low=24400.0,
        expiry_date=date(2026, 7, 7),
        system_status="OK",
        system_status_detail={"ws": "OK", "option_chain": "OK"},
    )
    assert snapshot.futures_basis == 50.0
    assert snapshot.india_vix == 13.2
    assert snapshot.gift_nifty is None
    assert snapshot.option_chain[0].strike == 24500
    assert snapshot.volume == 1_500_000
    assert snapshot.total_buy_quantity == 800_000
    assert snapshot.total_sell_quantity == 700_000
    assert snapshot.day_high == 24600.0
    assert snapshot.day_low == 24400.0


def test_snapshot_tolerates_fully_missing_data_as_none_not_defaults():
    snapshot = IngestionSnapshot(
        ts=datetime.now(timezone.utc),
        spot_ltp=None,
        futures_ltp=None,
        futures_basis=None,
        depth=None,
        option_chain=[],
        india_vix=None,
        gift_nifty=None,
        volume=None,
        total_buy_quantity=None,
        total_sell_quantity=None,
        day_high=None,
        day_low=None,
        expiry_date=None,
        system_status="DISCONNECTED",
        system_status_detail={"ws": "DISCONNECTED", "option_chain": "DISCONNECTED"},
    )
    assert snapshot.spot_ltp is None
    assert snapshot.futures_ltp is None
    assert snapshot.india_vix is None
    assert snapshot.option_chain == []
    assert snapshot.volume is None
    assert snapshot.day_high is None


def test_option_strike_has_valid_greeks_predicate():
    valid_strike = OptionStrike(
        strike=24500,
        call_oi=1000,
        put_oi=1200,
        call_iv=14.2,
        put_iv=14.5,
        call_greeks=Greeks(delta=0.5, gamma=0.01, theta=-2.0, vega=1.2),
        put_greeks=Greeks(delta=-0.5, gamma=0.01, theta=-2.0, vega=1.2),
    )
    assert valid_strike.has_valid_greeks is True

    zero_oi_strike = OptionStrike(
        strike=24500,
        call_oi=0,
        put_oi=0,
        call_iv=0.0,
        put_iv=0.0,
        call_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
        put_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
    )
    assert zero_oi_strike.has_valid_greeks is True

    zero_call_gamma_with_oi = OptionStrike(
        strike=23000,
        call_oi=121615,
        put_oi=0,
        call_iv=0.0,
        put_iv=0.0,
        call_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
        put_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
    )
    assert zero_call_gamma_with_oi.has_valid_greeks is False
    assert zero_call_gamma_with_oi.has_valid_call_greeks is False
    assert zero_call_gamma_with_oi.has_valid_put_greeks is True

    zero_put_gamma_with_oi = OptionStrike(
        strike=23000,
        call_oi=0,
        put_oi=50000,
        call_iv=0.0,
        put_iv=0.0,
        call_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
        put_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
    )
    assert zero_put_gamma_with_oi.has_valid_greeks is False
    assert zero_put_gamma_with_oi.has_valid_call_greeks is True
    assert zero_put_gamma_with_oi.has_valid_put_greeks is False

    # Mixed-leg: one leg invalid (call zeroed), opposite leg valid (put has valid gamma & OI)
    mixed_call_zeroed = OptionStrike(
        strike=23000,
        call_oi=121615,
        put_oi=50000,
        call_iv=0.0,
        put_iv=14.0,
        call_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
        put_greeks=Greeks(delta=-0.5, gamma=0.01, theta=-2.0, vega=1.2),
    )
    assert mixed_call_zeroed.has_valid_call_greeks is False
    assert mixed_call_zeroed.has_valid_put_greeks is True
    assert mixed_call_zeroed.has_valid_greeks is False

    # Mixed-leg: opposite leg invalid (put zeroed), call leg valid
    mixed_put_zeroed = OptionStrike(
        strike=23000,
        call_oi=121615,
        put_oi=50000,
        call_iv=14.0,
        put_iv=0.0,
        call_greeks=Greeks(delta=0.5, gamma=0.01, theta=-2.0, vega=1.2),
        put_greeks=Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0),
    )
    assert mixed_put_zeroed.has_valid_call_greeks is True
    assert mixed_put_zeroed.has_valid_put_greeks is False
    assert mixed_put_zeroed.has_valid_greeks is False


