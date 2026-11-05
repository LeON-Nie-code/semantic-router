"""Authoring contract for the unified shadow_dispatch plugin."""

import pytest
from cli.models import PluginConfig, PluginType, ShadowDispatchPluginConfig
from pydantic import ValidationError


def test_minimal_config_and_runtime_defaults():
    cfg = ShadowDispatchPluginConfig()
    assert cfg.enabled is False
    assert cfg.models == []
    assert cfg.sample_rate is None
    assert cfg.execution.max_concurrency is None
    assert cfg.execution.max_queue_size is None
    assert cfg.execution.timeout == "30s"
    assert cfg.budget.max_calls is None
    assert cfg.budget.max_response_bytes is None
    assert cfg.capture.enabled is False
    assert cfg.capture.max_bytes is None


def test_enabled_models_are_trimmed_and_round_trip():
    cfg = ShadowDispatchPluginConfig(enabled=True, models=[" candidate ", "other"])
    assert cfg.models == ["candidate", "other"]
    plugin = PluginConfig(type="shadow_dispatch", configuration=cfg.model_dump())
    assert plugin.type is PluginType.SHADOW_DISPATCH
    assert plugin.model_dump()["configuration"]["models"] == ["candidate", "other"]


@pytest.mark.parametrize("models", [[], [" "], ["a", " a "], ["a"] * 9, [1], "a", None])
def test_invalid_models_rejected(models):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(enabled=True, models=models)


def test_eight_distinct_models_allowed():
    assert (
        len(ShadowDispatchPluginConfig(enabled=True, models=list("abcdefgh")).models)
        == 8
    )


@pytest.mark.parametrize("models", [[""], ["a", " a "], list("abcdefghi")])
def test_disabled_config_still_validates_models(models):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(models=models)


@pytest.mark.parametrize(
    "value", [-0.1, 1.1, float("nan"), float("inf"), -float("inf")]
)
def test_invalid_sample_rate_rejected(value):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(sample_rate=value)


@pytest.mark.parametrize("value", [None, 0, 0.5, 1])
def test_sample_rate_boundaries(value):
    assert ShadowDispatchPluginConfig(sample_rate=value).sample_rate == value


NUMERIC_FIELDS = [
    ("execution", "max_concurrency"),
    ("execution", "max_queue_size"),
    ("budget", "max_calls"),
    ("budget", "max_response_bytes"),
    ("capture", "max_bytes"),
]


@pytest.mark.parametrize("section,field", NUMERIC_FIELDS)
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, 1.5, "2"])
def test_numeric_limits_are_strict_positive_integers(section, field, value):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(**{section: {field: value}})


@pytest.mark.parametrize("section,field", NUMERIC_FIELDS)
@pytest.mark.parametrize("value", [None, 1, 17])
def test_numeric_defaults_and_valid_values(section, field, value):
    cfg = ShadowDispatchPluginConfig(**{section: {field: value}})
    assert getattr(getattr(cfg, section), field) == value


@pytest.mark.parametrize("field", ["max_calls", "max_response_bytes"])
def test_budget_covers_every_model(field):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(models=["a", "b"], budget={field: 1})
    ShadowDispatchPluginConfig(
        models=["a", "b"], budget={field: 2 if field == "max_calls" else 4}
    )


@pytest.mark.parametrize(
    "timeout",
    [
        "1ms",
        "0.5s",
        "1m30s",
        "2m500ms",
        "5m",
        "300s",
        "1.5m",
        "300.0000000009s",
        "+1s",
        ".5s",
        "1.s",
        "0.000000001s",
    ],
)
def test_go_style_positive_duration(timeout):
    cfg = ShadowDispatchPluginConfig(execution={"timeout": timeout})
    assert cfg.execution.timeout == timeout


@pytest.mark.parametrize(
    "timeout",
    [
        "0s",
        "0ms",
        "-1s",
        "5m1ms",
        "301s",
        "1h",
        "30",
        "",
        "nan",
        "1s junk",
        " 1s",
        30,
        None,
        "0.0000000001s",
    ],
)
def test_invalid_or_out_of_bounds_duration(timeout):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(execution={"timeout": timeout})


@pytest.mark.parametrize("section", [None, "capture"])
@pytest.mark.parametrize("value", [None, "true", 1])
def test_enabled_flags_are_strict_booleans(section, value):
    config = {"enabled": value} if section is None else {section: {"enabled": value}}
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(**config)


@pytest.mark.parametrize(
    "field",
    [
        "model",
        "arms",
        "max_concurrency",
        "max_queue_depth",
        "timeout_seconds",
        "max_response_bytes",
        "max_retries",
        "capture_response_body",
        "max_capture_bytes",
        "tls_skip_verify",
        "forward_headers",
        "bogus",
    ],
)
def test_old_and_unknown_top_level_fields_rejected(field):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(**{field: None})


@pytest.mark.parametrize("section", ["execution", "budget", "capture"])
@pytest.mark.parametrize(
    "field",
    [
        "bogus",
        "max_retries",
        "tls_skip_verify",
        "forward_headers",
    ],
)
def test_unknown_nested_fields_rejected(section, field):
    with pytest.raises(ValidationError):
        ShadowDispatchPluginConfig(**{section: {field: None}})
