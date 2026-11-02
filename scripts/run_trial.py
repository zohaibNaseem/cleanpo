"""Run a free test for a new client.

  python scripts/run_trial.py --orders <folder with order files> --price-list <file> --out <folder>
                              [--customers <file>] [--model claude-opus-5-5]

Without the ANTHROPIC_API_KEY environment variable, only the sample orders (saved readings) can
be read. Keep the output folder outside the repository (data/ is not committed), and delete the
client's files after the test.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cleanpo.pricelist import PriceListError  # noqa: E402
from cleanpo.reader import Reader  # noqa: E402
from cleanpo.trial import run_trial  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a CleanPO free test.")
    parser.add_argument("--orders", required=True, type=Path, help="folder with the client's order files")
    parser.add_argument("--price-list", required=True, type=Path, help="the client's price list, any layout")
    parser.add_argument("--out", required=True, type=Path, help="output folder")
    parser.add_argument("--customers", type=Path, help="the client's customer list (optional)")
    parser.add_argument("--model", default="claude-opus-5-5")
    args = parser.parse_args()

    reader = Reader(mode="auto", model=args.model, replay_dir=ROOT / "demo_data" / "readings")
    if not reader.uses_ai:
        print("No ANTHROPIC_API_KEY set: only files with a saved demo reading can be read.")
    try:
        result = run_trial(args.orders, args.price_list, args.out, reader, args.customers,
                           price_list_replay_dir=ROOT / "demo_data" / "price_lists" / "readings")
    except (PriceListError, FileNotFoundError, ValueError) as exc:
        print(f"Stopped: {exc}")
        return 1

    pl = result.price_list
    print(f"Price list: {len(pl.products)} products loaded ({pl.method}), {pl.skipped} rows not loaded.")
    print(f"Orders: {len(result.orders)}. Ready: {result.count('ready')}. "
          f"Need a person: {result.count('needs_review')}. Not orders: {result.count('not_order')}.")
    print(f"API cost: ${result.api_cost_usd:.4f}")
    print(f"Import file: {result.import_file}")
    print(f"Report: {result.report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
