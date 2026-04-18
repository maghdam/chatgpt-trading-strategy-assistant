import argparse
import json
import sys
import urllib.error
import urllib.request


def request_json(base_url: str, method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method.upper(),
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def print_step(title: str):
    print(f"\n== {title} ==")


def main():
    parser = argparse.ArgumentParser(
        description="Basic smoke test for the trading assistant backend.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL. Default: http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--symbol",
        default="EURUSD",
        help="Symbol to use for fetch/analyze checks. Default: EURUSD",
    )
    parser.add_argument(
        "--timeframe",
        default="M15",
        help="Timeframe used for /fetch-data. Default: M15",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=50,
        help="Number of bars to fetch. Default: 50",
    )
    args = parser.parse_args()

    try:
        print_step("Health")
        status, health = request_json(args.base_url, "GET", "/health")
        print(f"HTTP {status}")
        print(json.dumps(health, indent=2))

        print_step("Fetch Data")
        status, market = request_json(
            args.base_url,
            "POST",
            "/fetch-data",
            {
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "num_bars": args.bars,
                "return_chart": False,
            },
        )
        print(f"HTTP {status}")
        print(
            json.dumps(
                {
                    "symbol": market.get("symbol"),
                    "timeframe": market.get("timeframe"),
                    "bars_returned": len(market.get("ohlc", [])),
                    "context": market.get("context", {}),
                    "trend": market.get("trend", {}),
                },
                indent=2,
            )
        )

        print_step("Analyze")
        status, analysis = request_json(
            args.base_url,
            "POST",
            "/analyze",
            {"symbol": args.symbol},
        )
        print(f"HTTP {status}")
        print(
            json.dumps(
                {
                    "HTF_Bias": analysis.get("HTF_Bias"),
                    "LTF_Entry": analysis.get("LTF_Entry"),
                    "Confluence": analysis.get("Confluence"),
                    "ChecklistKeys": list((analysis.get("Checklist") or {}).keys()),
                },
                indent=2,
            )
        )
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error {exc.code}: {body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
