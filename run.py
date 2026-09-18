#!/usr/bin/env python3
"""開発用の起動スクリプト。

    python run.py                 # http://127.0.0.1:8000
    python run.py --port 9000 --reload

``if __name__ == "__main__"`` のガードが必須である点に注意してください。
判定ワーカーは ``spawn`` 方式の子プロセスとして起動するため、
ガードのないスクリプトから起動すると子プロセスが親を再実行してしまいます。
(``uvicorn app.main:app`` のように CLI から起動する場合は問題ありません。)
"""

from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="ExactRoom を起動する")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="開発用の自動リロード")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app", host=args.host, port=args.port, reload=args.reload
    )


if __name__ == "__main__":
    main()
