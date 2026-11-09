"""Compare generated expressions with an independent Krill price calculation.

The tiny evaluator supports only the expression grammar emitted by build.py;
New API's actual compiler and import/save paths are checked separately.
"""

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'\s*("(?:\\.|[^"\\])*"|\d+(?:\.\d+)?|[A-Za-z_][A-Za-z_0-9]*|<=|>=|==|&&|\|\||[()+*?:,<>=])')


def evaluate(expression, variables, service_tier=None, hour=0):
    tokens, pos = [], 0
    while pos < len(expression):
        match = TOKEN.match(expression, pos)
        if not match:
            raise ValueError("unrecognized expression token")
        tokens.append(match.group(1))
        pos = match.end()
    index = 0

    def take(value=None):
        nonlocal index
        token = tokens[index]
        if value is not None and token != value:
            raise ValueError(f"expected {value}, got {token}")
        index += 1
        return token

    def peek():
        return tokens[index] if index < len(tokens) else None

    def primary():
        token = take()
        if token == "(":
            value = conditional()
            take(")")
            return value
        if token.startswith('"'):
            return json.loads(token)
        if token[0].isdigit():
            return Decimal(token)
        if peek() == "(":
            take("(")
            arguments = [conditional()]
            while peek() == ",":
                take(",")
                arguments.append(conditional())
            take(")")
            if token == "tier":
                return arguments[1]
            if token == "fixed":
                return arguments[0] * 1000000
            if token == "hour":
                return hour
            if token == "param" and arguments[0] == "service_tier":
                return service_tier
            raise ValueError("unsupported function: " + token)
        return variables[token]

    def product():
        value = primary()
        while peek() == "*":
            take("*")
            value *= primary()
        return value

    def addition():
        value = product()
        while peek() == "+":
            take("+")
            value += product()
        return value

    def comparison():
        value = addition()
        while peek() in ("<", "<=", ">", ">=", "=="):
            operator = take()
            right = addition()
            if operator == "==":
                value = value == right
            elif operator == "<":
                value = value < right
            elif operator == "<=":
                value = value <= right
            elif operator == ">":
                value = value > right
            else:
                value = value >= right
        return value

    def logical_and():
        value = comparison()
        while peek() == "&&":
            take("&&")
            right = comparison()
            value = bool(value) and bool(right)
        return value

    def logical_or():
        value = logical_and()
        while peek() == "||":
            take("||")
            right = logical_and()
            value = bool(value) or bool(right)
        return value

    def conditional():
        value = logical_or()
        if peek() == "?":
            take("?")
            yes = conditional()
            take(":")
            no = conditional()
            return yes if value else no
        return value

    result = conditional()
    if index != len(tokens):
        raise ValueError("trailing expression tokens")
    return result


def expected_price(model, variables, minute, service_tier, by_name):
    if service_tier in ("fast", "priority") and model["provider"] == "OpenAI":
        model = by_name.get(model["model_name"] + "-fast", model)
    prices = model
    high = model.get("high_context_rule")
    if high and high["enabled"] and variables["len"] > high["input_tokens_threshold"]:
        prices = high
    for window in model.get("time_window_rules", []):
        if not window["enabled"]:
            continue
        start, end = window["start_minute"], window["end_minute"]
        matches = start <= minute < end if start < end else minute >= start or minute < end
        if matches:
            prices = window
            break
    cost = sum(variables[var] * Decimal(prices[field]) for var, field in (
        ("p", "input_token_price"), ("c", "output_token_price"), ("cr", "cache_read_input_token_price")
    ))
    split_cache = model["cache_creation_5m_token_price"] is not None and model["cache_creation_1h_token_price"] is not None
    for variable, field in (("cc", "cache_creation_5m_token_price"), ("cc1h", "cache_creation_1h_token_price")):
        value = prices.get(field) if split_cache else None
        if value is None:
            value = prices["cache_creation_token_price"]
        cost += variables[variable] * Decimal(value)
    return cost


def check():
    raw = (ROOT / "source/krill/model-pricing.json").read_bytes()
    models = json.loads(raw)["data"]
    by_name = {model["model_name"]: model for model in models}
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(raw).hexdigest() == manifest["source_sha256"]
    assert json.loads((ROOT / "api/krill/models.json").read_text(encoding="utf-8")) == json.loads(raw)
    data = json.loads((ROOT / "api/newapi/ratio_config.json").read_text(encoding="utf-8"))["data"]
    assert set(data["billing_mode"]) == set(by_name)
    metadata = json.loads((ROOT / "api/newapi/models.json").read_text(encoding="utf-8"))["data"]
    assert {m["model_name"] for m in metadata} == set(by_name)
    assert all(m["description"] == m["tags"] == m["icon"] == "" and m["endpoints"] is None for m in metadata)
    cases = 0
    for model in models:
        name, mode = model["model_name"], model["billing_mode"]
        if mode == "duration":
            assert Decimal(str(data["model_price"][name])) == Decimal(model["request_price_usd"])
            cases += 1
            continue
        if mode == "request":
            assert evaluate(data["billing_expr"][name], {}) == Decimal(model["request_price_usd"]) * 1000000
            cases += 1
            continue
        lengths = {0, 1000}
        related = [model, by_name.get(name + "-fast", model)]
        for item in related:
            high = item.get("high_context_rule")
            if high and high["enabled"]:
                threshold = high["input_tokens_threshold"]
                lengths.update((threshold - 1, threshold, threshold + 1))
        minutes = range(1440) if model["time_window_rules"] else (0, 540, 720, 840, 1080, 1439)
        for length in sorted(lengths):
            for minute in minutes:
                for speed in (None, "default", "fast", "priority"):
                    variables = {"p": Decimal(1234), "c": Decimal(567), "cr": Decimal(890),
                                 "cc": Decimal(321), "cc1h": Decimal(123), "len": Decimal(length)}
                    actual = evaluate(data["billing_expr"][name], variables, speed, minute // 60)
                    expected = expected_price(model, variables, minute, speed, by_name)
                    assert actual == expected, (name, length, minute, speed, actual, expected)
                    cases += 1
    return {"success": True, "models": len(models), "price_scenarios": cases,
            "deepseek_minutes_checked": 1440, "source_preserved": True,
            "metadata_enrichment_added": False,
            "scope": "Offline expression-versus-source price comparison, including every minute of DeepSeek windows and exact context thresholds"}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
