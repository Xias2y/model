"""Export a captured Krill price response in New API's native catalog formats."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICE_FIELDS = {
    "p": "input_token_price",
    "c": "output_token_price",
    "cr": "cache_read_input_token_price",
}


def coefficient(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError(f"invalid price: {value}")
    return format(number.normalize(), "f")


def token_leaf(prices, model, label):
    terms = [f"{var} * {coefficient(prices[field])}" for var, field in PRICE_FIELDS.items()]
    split_cache = all(model.get(k) is not None for k in (
        "cache_creation_5m_token_price", "cache_creation_1h_token_price"
    ))
    if split_cache:
        writes = {"cc": "cache_creation_5m_token_price", "cc1h": "cache_creation_1h_token_price"}
    else:
        writes = {"cc": "cache_creation_token_price", "cc1h": "cache_creation_token_price"}
    for var, field in writes.items():
        value = prices.get(field)
        if value is None:
            value = prices["cache_creation_token_price"]
        terms.append(f"{var} * {coefficient(value)}")
    return f"tier({json.dumps(label)}, " + " + ".join(terms) + ")"


def model_expression(model, timezone):
    expression = token_leaf(model, model, "standard")
    high = model.get("high_context_rule")
    windows = [w for w in model.get("time_window_rules", []) if w.get("enabled")]
    if high and high.get("enabled"):
        if windows:
            raise ValueError("upstream time/context precedence is unspecified")
        expression = (
            f"len <= {high['input_tokens_threshold']} ? {expression} : "
            + token_leaf(high, model, "long_context")
        )
    for window in reversed(windows):
        if not timezone:
            raise ValueError("time-window export requires an explicitly selected timezone")
        start, end = window["start_minute"], window["end_minute"]
        if start % 60 or end % 60:
            raise ValueError("new minute-level window needs an explicit adapter update")
        hour = f"hour({json.dumps(timezone)})"
        join = "&&" if start < end else "||"
        condition = f"({hour} >= {start // 60} {join} {hour} < {end // 60})"
        expression = (
            f"{condition} ? {token_leaf(window, model, 'off_peak')} : ({expression})"
        )
    return expression


def write_json(root, path, value):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(root=ROOT):
    raw_bytes = (root / "source/krill/model-pricing.json").read_bytes()
    source = json.loads(raw_bytes)
    if source.get("success") is not True:
        raise ValueError("Krill capture did not report success")
    models = source["data"]
    names = [m["model_name"] for m in models]
    if len(set(names)) != len(names):
        raise ValueError("duplicate upstream model names")
    adapter = json.loads((root / "adapter.json").read_text(encoding="utf-8"))
    timezone = adapter["time_window_timezone"]
    by_name = {m["model_name"]: m for m in models}
    expressions = {}
    modes = {}
    fixed_prices = {}
    billing_units = {}
    for model in models:
        name, mode = model["model_name"], model["billing_mode"]
        if mode == "token":
            expressions[name] = model_expression(model, timezone)
            modes[name] = "tiered_expr"
            billing_units[name] = "USD / 1M tokens"
        elif mode == "request":
            expressions[name] = f'tier("request", fixed({coefficient(model["request_price_usd"])}))'
            modes[name] = "tiered_expr"
            billing_units[name] = "USD / request"
        elif mode == "duration":
            # Legacy video adaptors multiply ModelPrice by duration. An
            # arbitrary task plugin must supply its own seconds usage schema.
            fixed_prices[name] = float(Decimal(model["request_price_usd"]))
            modes[name] = "ratio"
            billing_units[name] = "USD / second; requires a duration-settling video adaptor"
        else:
            raise ValueError(f"unsupported upstream billing mode: {mode}")

    speed_models = {}
    for model in models:
        if model["provider"] != "OpenAI" or model["billing_mode"] != "token":
            continue
        name = model["model_name"]
        if name.endswith(("-fast", "-ultrafast")):
            continue
        variants = {"standard": name}
        for speed in ("fast", "ultrafast"):
            if name + "-" + speed in by_name:
                variants[speed] = name + "-" + speed
        if len(variants) > 1:
            speed_models[name] = variants
        if "fast" in variants:
            fast_expr = expressions[variants["fast"]]
            standard_expr = expressions[name]
            expressions[name] = (
                '(param("service_tier") == "priority" || param("service_tier") == "fast")'
                f" ? ({fast_expr}) : ({standard_expr})"
            )

    metadata = [{
        "model_name": m["model_name"],
        "description": m.get("description") or "",
        "tags": m.get("tags") or "",
        "icon": m.get("icon") or "",
        "endpoints": m.get("endpoints"),
        "vendor_name": m["provider"],
        "name_rule": 0,
        "status": 1 if m["enabled"] else 0,
    } for m in models]
    vendors = [{"name": name, "description": "", "icon": "", "status": 1}
               for name in sorted({m["provider"] for m in models})]
    envelope = lambda rows: {"success": True, "message": "", "data": rows}
    for locale in (None, "zh", "en", "ja"):
        prefix = "api/newapi" if locale is None else f"api/i18n/{locale}/newapi"
        # No translation or capability enrichment: every locale has the same
        # Krill-sourced metadata, exactly as the default source routes expect.
        write_json(root, prefix + "/models.json", envelope(metadata))
        write_json(root, prefix + "/vendors.json", envelope(vendors))
    pricing = {"billing_mode": modes, "billing_expr": expressions, "model_price": fixed_prices}
    write_json(root, "api/newapi/ratio_config.json", envelope(pricing))
    write_json(root, "api/ratio_config.json", envelope(pricing))
    write_json(root, "api/newapi/billing-units.json", billing_units)
    write_json(root, "api/newapi/speed-models.json", speed_models)
    write_json(root, "api/krill/models.json", source)
    manifest = {
        "source": "https://www.krill-code.com/api/model-pricing",
        "capture": json.loads((root / "source/krill/capture.json").read_text(encoding="utf-8")),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "model_count": len(models),
        "token_models": sum(m["billing_mode"] == "token" for m in models),
        "request_models": sum(m["billing_mode"] == "request" for m in models),
        "duration_models": len(fixed_prices),
        "fast_models": sum(m["model_name"].endswith("-fast") for m in models),
        "ultrafast_models": sum(m["model_name"].endswith("-ultrafast") for m in models),
        "long_context_models": sum(bool(m.get("high_context_rule", {}).get("enabled"))
                                   for m in models if m.get("high_context_rule")),
        "time_window_models": sum(any(w.get("enabled") for w in m.get("time_window_rules", [])) for m in models),
        "metadata_source": "Krill only; unavailable descriptions/tags/icons/endpoints remain empty",
        "user_group_multipliers_included": False,
        "account_route_multipliers_applied": False,
        "adaptor": adapter,
        "limits": [
            "Krill does not explicitly supply a timezone or start-versus-completion time policy.",
            "The chosen timezone is an explicit deployment adapter setting, not a Krill source field.",
            "Duration prices require a video adaptor that settles seconds; a generic per-request or task-plugin route is not equivalent.",
            "Priority forwarding and suffix alias routing are channel configuration; price synchronization does not configure them.",
            "Ultrafast SKUs are preserved; no unverified service_tier value is invented.",
            "Native metadata synchronization does not store all Krill limits and modality fields; the raw catalog preserves them.",
        ],
    }
    write_json(root, "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    result = build(arguments.root)
    print(json.dumps({"models": result["model_count"], "source_sha256": result["source_sha256"]}))
