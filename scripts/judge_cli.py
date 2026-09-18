#!/usr/bin/env python3
"""コマンドラインから判定を試すツール (サーバ不要)。

出題前に模範解答と想定される別解の組み合わせを一括で確認するのに使います。

例:
    python scripts/judge_cli.py '\\frac{1}{2}' '0.5' '\\frac{2}{4}' '0.6'
    python scripts/judge_cli.py --assume-positive '\\ln(x^2)' '2\\ln x'
    python scripts/judge_cli.py --file cases.tsv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mathjudge.equivalence import judge  # noqa: E402

COLORS = {"AC": "\033[32m", "WA": "\033[31m", "PENDING": "\033[33m"}
RESET = "\033[0m"


def run(model: str, candidates: list[str], options: dict, color: bool) -> int:
    failures = 0
    print(f"模範解答: {model}")
    print("-" * 72)
    for candidate in candidates:
        result = judge(model, candidate, options)
        tint = COLORS.get(result.verdict, "") if color else ""
        end = RESET if color and tint else ""
        print(
            f"  {candidate:<34s} -> {tint}{result.verdict:<8s}{end}"
            f" {result.reason:<28s} {result.elapsed_ms:>5d} ms"
        )
        if result.verdict == "PENDING":
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="ExactRoom 判定エンジンの CLI")
    parser.add_argument("model", nargs="?", help="模範解答 (TeX)")
    parser.add_argument("candidates", nargs="*", help="判定したい答案 (TeX)")
    parser.add_argument("--file", help="タブ区切りの 模範解答<TAB>答案 のファイル")
    parser.add_argument("--no-euler-e", action="store_true", help="e を変数として扱う")
    parser.add_argument("--no-imaginary-i", action="store_true", help="i を変数として扱う")
    parser.add_argument("--no-assume-real", action="store_true", help="実数の仮定を外す")
    parser.add_argument("--assume-positive", action="store_true", help="変数を正と仮定する")
    parser.add_argument("--log-base", choices=["e", "10"], default="e")
    parser.add_argument("--ordered-list", action="store_true", help="複数解の順序も一致させる")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    options = {
        "euler_e": not args.no_euler_e,
        "imaginary_i": not args.no_imaginary_i,
        "assume_real": not args.no_assume_real,
        "assume_positive": args.assume_positive,
        "log_base": args.log_base,
        "ordered_list": args.ordered_list,
    }
    color = not args.no_color and sys.stdout.isatty()

    if args.file:
        pending = 0
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                print(f"  (スキップ) {line}", file=sys.stderr)
                continue
            pending += run(parts[0], parts[1:], options, color)
            print()
        return 0

    if not args.model or not args.candidates:
        parser.print_help()
        return 2
    run(args.model, args.candidates, options, color)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
