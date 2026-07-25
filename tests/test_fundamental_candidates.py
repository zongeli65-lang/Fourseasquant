from datetime import date

from fourseasquant.fundamental_candidates import (
    build_fundamental_candidate_pool,
)


def test_candidate_pool_keeps_140_40_20_cold_name_coverage() -> None:
    market_codes = [f"{index:06d}" for index in range(1, 401)]
    board_members = {
        f"board-{board}": market_codes[140 + board * 20 : 160 + board * 20]
        for board in range(5)
    }

    pool = build_fundamental_candidate_pool(
        as_of_date=date(2026, 7, 25),
        heat_codes=market_codes[:100],
        technical_codes=market_codes[40:140],
        announcement_codes=market_codes[80:180],
        board_members=board_members,
        market_codes=market_codes,
    )

    assert len(pool.primary_codes) == 140
    assert len(pool.board_sample_codes) == 40
    assert len(pool.random_sample_codes) == 20
    assert len(pool.codes) == 200
    assert set(pool.codes).issubset(set(market_codes))
    assert set(pool.primary_codes).isdisjoint(pool.board_sample_codes)
    assert set(pool.codes[180:]).isdisjoint(pool.codes[:180])
    assert any(code in pool.primary_codes for code in market_codes[:40])
    assert any(code in pool.primary_codes for code in market_codes[120:180])


def test_candidate_pool_is_deterministic_for_the_same_date() -> None:
    market_codes = [f"{index:06d}" for index in range(1, 301)]
    first = build_fundamental_candidate_pool(
        as_of_date=date(2026, 7, 25),
        heat_codes=market_codes[:80],
        technical_codes=market_codes[80:160],
        announcement_codes=market_codes[160:200],
        board_members={"board": market_codes[200:260]},
        market_codes=market_codes,
    )
    second = build_fundamental_candidate_pool(
        as_of_date=date(2026, 7, 25),
        heat_codes=market_codes[:80],
        technical_codes=market_codes[80:160],
        announcement_codes=market_codes[160:200],
        board_members={"board": market_codes[200:260]},
        market_codes=market_codes,
    )

    assert first == second
