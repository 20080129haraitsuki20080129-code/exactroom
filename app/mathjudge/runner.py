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

from .equivalence import PENDING, JudgeResult, judge

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
                verdict=PENDING,
                reason="timeout",
                detail=f"判定が {timeout:.0f} 秒以内に終わりませんでした。",
                student_message="判定に時間がかかりすぎました (判定保留)。",
            ).to_dict()
        except Exception as exc:
            with self._lock:
                self._reset_pool()
            return JudgeResult(
                verdict=PENDING,
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


def get_runner():
    """環境変数 ``JUDGE_ISOLATION`` に従ってランナーを返す。

    * ``process`` (既定) … 別プロセス + ハードタイムアウト
    * ``inline``          … 同一プロセス (CI やサーバレス環境向け)
    """
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                mode = os.getenv("JUDGE_ISOLATION", "process").lower()
                if mode == "inline":
                    _runner = InlineRunner()
                else:
                    workers = int(os.getenv("JUDGE_WORKERS", "2"))
                    _runner = JudgePool(workers=workers)
    return _runner


def reset_runner() -> None:
    """テスト用: ランナーを破棄する。"""
    global _runner
    with _runner_lock:
        if _runner is not None:
            _runner.shutdown()
        _runner = None


def judge_isolated(
    model_latex: str,
    submitted_latex: str,
    options: dict | None = None,
    timeout: float | None = None,
) -> dict:
    timeout = timeout or float(os.getenv("JUDGE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
    return get_runner().run(model_latex, submitted_latex, options, timeout=timeout)
