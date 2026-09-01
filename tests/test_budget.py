import pytest

from pipeline.budget import BudgetExceeded, Ledger, cost_for
from pipeline.config import load_config
from pipeline.contracts import Usage


def test_config_loads_stage_caps():
    cfg = load_config("pipeline.toml")
    assert cfg.stages["build"].max_turns == 50
    assert cfg.stages["verify"].per_command_seconds["next_build"] == 300
    assert cfg.run.max_cost_usd == 2.0


def test_cost_for_applies_price_table():
    cfg = load_config("pipeline.toml")
    u = Usage(input_tokens=1_000_000, output_tokens=100_000,
              cache_creation_input_tokens=200_000, cache_read_input_tokens=400_000)
    assert cost_for(u, "claude-haiku-4-5-20251001", cfg) == pytest.approx(1.79)


def test_cost_for_unknown_model_is_zero_and_flagged():
    cfg = load_config("pipeline.toml")
    assert cost_for(Usage(input_tokens=10), "mystery", cfg) == 0.0


def test_ledger_trips_run_cost_cap_at_boundary():
    led = Ledger(max_cost_usd=1.0, max_seconds=100)
    led.add(cost_usd=0.6, wall_ms=1000)
    led.check()
    led.add(cost_usd=0.4, wall_ms=1000)
    led.check()  # exactly at cap is allowed
    led.add(cost_usd=0.01, wall_ms=0)
    with pytest.raises(BudgetExceeded) as ei:
        led.check()
    assert ei.value.snapshot.cost_used == pytest.approx(1.01)
    assert ei.value.snapshot.cost_cap == 1.0


def test_ledger_trips_run_time_cap():
    led = Ledger(max_cost_usd=10.0, max_seconds=1.0)
    led.add(cost_usd=0, wall_ms=1500)
    with pytest.raises(BudgetExceeded, match="seconds"):
        led.check()
