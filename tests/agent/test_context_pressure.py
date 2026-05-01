from agent.context_pressure import classify_context_pressure, resolve_context_pressure_bands


def test_resolves_gpt55_sized_pressure_bands() -> None:
    bands = resolve_context_pressure_bands(context_length=272_000, config={})

    assert bands.soft_warning_tokens == 168_640
    assert bands.normal_compaction_tokens == 199_920
    assert bands.emergency_compaction_tokens == 220_320
    assert bands.hard_limit_tokens == 235_280


def test_existing_threshold_config_remains_normal_threshold() -> None:
    bands = resolve_context_pressure_bands(
        context_length=272_000,
        config={"threshold": 0.50},
    )

    assert bands.normal_compaction_tokens == 136_000
    assert bands.soft_warning_tokens < bands.normal_compaction_tokens
    assert bands.emergency_compaction_tokens > bands.normal_compaction_tokens
    assert bands.hard_limit_tokens > bands.emergency_compaction_tokens


def test_pressure_bands_are_clamped_in_order() -> None:
    bands = resolve_context_pressure_bands(
        context_length=100_000,
        config={
            "soft_warning_threshold": 0.90,
            "threshold": 0.70,
            "emergency_threshold": 0.60,
            "hard_limit_threshold": 0.50,
        },
    )

    assert bands.soft_warning_tokens < bands.normal_compaction_tokens
    assert bands.normal_compaction_tokens < bands.emergency_compaction_tokens
    assert bands.emergency_compaction_tokens < bands.hard_limit_tokens


def test_pressure_bands_never_exceed_context_length() -> None:
    bands = resolve_context_pressure_bands(
        context_length=10_000,
        config={
            "soft_warning_threshold": 20_000,
            "threshold": 30_000,
            "emergency_threshold": 40_000,
            "hard_limit_threshold": 50_000,
        },
    )

    assert 0 < bands.soft_warning_tokens < bands.normal_compaction_tokens
    assert bands.normal_compaction_tokens < bands.emergency_compaction_tokens
    assert bands.emergency_compaction_tokens < bands.hard_limit_tokens
    assert bands.hard_limit_tokens <= bands.context_length


def test_absolute_token_overrides_are_supported() -> None:
    bands = resolve_context_pressure_bands(
        context_length=272_000,
        config={
            "soft_warning_threshold": 160_000,
            "threshold": 200_000,
            "emergency_threshold": 220_000,
            "hard_limit_threshold": 235_000,
        },
    )

    assert bands.soft_warning_tokens == 160_000
    assert bands.normal_compaction_tokens == 200_000
    assert bands.emergency_compaction_tokens == 220_000
    assert bands.hard_limit_tokens == 235_000


def test_classifies_current_token_pressure() -> None:
    bands = resolve_context_pressure_bands(context_length=272_000, config={})

    assert classify_context_pressure(100_000, bands) == "normal"
    assert classify_context_pressure(170_000, bands) == "soft"
    assert classify_context_pressure(201_000, bands) == "compaction"
    assert classify_context_pressure(221_000, bands) == "emergency"
    assert classify_context_pressure(236_000, bands) == "hard"
