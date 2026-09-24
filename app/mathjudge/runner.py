"""判定処理を別プロセスに隔離して実行する。

SymPy の ``simplify`` は入力によっては極端に時間がかかる。
Web アプリのワーカースレッドを占有させないため、また万一暴走しても
確実に止められるようにするため、判定はワーカープロセスで実行し
ハードタイムアウトを掛ける。
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import sys
import threading

from .equivalence import INTERNAL_ERROR, JudgeResult, judge

DEFAULT_TIMEOUT_SEC = 8.0


def _worker_init() -> None:  # pragma: no cover - 子プロセス側
    # 子プロセスでは再帰上限を控えめにしておく
    sys.setrecursionlimit(3000)
    with contextlib.suppress(Exception):
        import sympy  # noqa: F401  (起動時に import コストを払っておく)


def _worker_judge(model_latex: str, submitted_latex: str, options: dict) -> dict:
    """ワーカープロセスで実行される関数 (ピクル可能である必要がある)。"""
    return judge(model_latex, submitted_latex, options).to_dict()


class JudgePool:
    """タイムアウト時に確実に殺せるワーカープール。"""

    def __init__(self, workers: int = 2, max_tasks_per_child: int = 40) -> None:
        self._workers = max(1, workers)
        self._max_tasks = max_tasks_per_child
        self._pool: multiprocessing.pool.Pool | None = None
        self._lock = threading.Lock()

    def _ensure_pool(self) -> multiprocessing.pool.Pool:
        if self._pool is None:
            ctx = multiprocessing.get_context("spawn")
            self._pool = ctx.Pool(
                processes=self._workers,
                initializer=_worker_init,
                maxtasksperchild=self._max_tasks,
            )
        return self._pool

    def _reset_pool(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.terminate()
                pool.join()
            except Exception:  # pragma: no cover - 防御的
                pass

    def run(
        self,
        model_latex: str,
        submitted_latex: str,
        options: dict | None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        _retry: bool = True,
    ) -> dict:
        with self._lock:
            pool = self._ensure_pool()
            async_result = pool.apply_async(
                _worker_judge, (model_latex, submitted_latex, dict(options or {}))
            )
        try:
            return async_result.get(timeout=timeout)
        except multiprocessing.TimeoutError:
            with self._lock:
                self._reset_pool()
            return JudgeResult(
                status="timeout",
                verdict=None,
                reason="timeout",
                detail=f"判定が {timeout:.0f} 秒以内に終わりませんでした。",
                student_message="判定に時間がかかりすぎました (判定保留)。",
            ).to_dict()
        except Exception as exc:
            with self._lock:
                self._reset_pool()
            if _retry:
                # 他の人の判定がタイムアウトしてプールごと作り直されると、
                # 同時に走っていた自分の判定まで巻き添えで落ちる。
                # 自分の入力に問題があるとは限らないので一度だけやり直す。
                return self.run(
                    model_latex, submitted_latex, options, timeout, _retry=False
                )
            return JudgeResult(
                status=INTERNAL_ERROR,
                verdict=None,
                reason="worker_error",
                detail=f"ワーカープロセスが異常終了しました: {type(exc).__name__}",
                student_message="判定に失敗しました (判定保留)。",
            ).to_dict()

    def shutdown(self) -> None:
        with self._lock:
            self._reset_pool()


class InlineRunner:
    """プロセス分離なしで同一プロセス内で判定する (テスト・制約環境用)。

    タイムアウトによる強制停止はできない点に注意。
    """

    def run(
        self,
        model_latex: str,
        submitted_latex: str,
        options: dict | None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> dict:
        return judge(model_latex, submitted_latex, dict(options or {})).to_dict()

    def shutdown(self) -> None:
        return None


_runner = None
_runner_lock = threading.Lock()
#: ``configure_runner()`` で明示的に設定された値 (未設定なら環境変数を見る)
_config: dict = {}


def configure_runner(
    isolation: str | None = None,
    workers: int | None = None,
    timeout: float | None = None,
) -> None:
    """判定ランナーの設定を明示的に与える。

    ``app/main.py`` の起動時に ``Settings`` の値を渡す。こうしないと
    ``.env`` にだけ書いた ``JUDGE_ISOLATION`` 等が無視されてしまう
    (``os.getenv`` はプロセス環境変数しか見ないため)。
    """
    global _runner
    with _runner_lock:
        if isolation is not None:
            _config["isolation"] = str(isolation).lower()
        if workers is not None:
            _config["workers"] = int(workers)
        if timeout is not None:
            _config["timeout"] = float(timeout)
        if _runner is not None:
            _runner.shutdown()
            _runner = None


def _setting(name: str, env: str, default):
    if name in _config:
        return _config[name]
    raw = os.getenv(env)
    if raw is None:
        return default
    try:
        return type(default)(raw)
    except (TypeError, ValueError):
        return default


def get_runner():
    """設定に従ってランナーを返す。

    * ``process`` (既定) … 別プロセス + ハードタイムアウト
    * ``inline``          … 同一プロセス (CI やサーバレス環境向け)
    """
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                mode = str(_setting("isolation", "JUDGE_ISOLATION", "process")).lower()
                if mode == "inline":
                    _runner = InlineRunner()
                else:
                    workers = int(_setting("workers", "JUDGE_WORKERS", 2))
                    _runner = JudgePool(workers=workers)
    return _runner


def reset_runner(clear_config: bool = False) -> None:
    """テスト用: ランナーを破棄する。"""
    global _runner
    with _runner_lock:
        if _runner is not None:
            _runner.shutdown()
        _runner = None
        if clear_config:
            _config.clear()


def judge_isolated(
    model_latex: str,
    submitted_latex: str,
    options: dict | None = None,
    timeout: float | None = None,
) -> dict:
    if timeout is None:
        timeout = float(_setting("timeout", "JUDGE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
    return get_runner().run(model_latex, submitted_latex, options, timeout=timeout)
