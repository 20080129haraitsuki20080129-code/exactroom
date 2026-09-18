"""判定のプロセス分離とタイムアウトのテスト。"""

from __future__ import annotations

import os
import time

import pytest

from app.mathjudge import runner


@pytest.fixture()
def process_runner(monkeypatch):
    monkeypatch.setenv("JUDGE_ISOLATION", "process")
    monkeypatch.setenv("JUDGE_WORKERS", "1")
    runner.reset_runner()
    yield runner
    runner.reset_runner()
    monkeypatch.setenv("JUDGE_ISOLATION", "inline")


def test_inline_runner_returns_dict():
    inline = runner.InlineRunner()
    result = inline.run(r"\frac{1}{2}", r"0.5", {})
    assert result["verdict"] == "AC"
    assert set(result) >= {"verdict", "reason", "student_message", "elapsed_ms"}


@pytest.mark.skipif(
    os.getenv("SKIP_PROCESS_TESTS") == "1", reason="プロセス分離テストを無効化"
)
def test_process_pool_judges_and_recovers(process_runner):
    pool = runner.JudgePool(workers=1)
    try:
        result = pool.run(r"x^2-1", r"(x-1)(x+1)", {}, timeout=60)
        assert result["verdict"] == "AC"

        # 極端に短いタイムアウトでは判定保留になり、プールは自動復旧する
        timed_out = pool.run(r"x^2-1", r"(x-1)(x+1)", {}, timeout=0.000001)
        assert timed_out["verdict"] == "PENDING"
        assert timed_out["reason"] in ("timeout", "worker_error")

        again = pool.run(r"\frac{1}{2}", r"0.5", {}, timeout=60)
        assert again["verdict"] == "AC"
    finally:
        pool.shutdown()


def test_timeout_never_leaks_model_answer():
    pool = runner.JudgePool(workers=1)
    try:
        secret = r"\frac{-1+\sqrt{17}}{4}"
        result = pool.run(secret, r"1", {}, timeout=0.000001)
        assert "sqrt{17}" not in str(result)
    finally:
        pool.shutdown()


def test_judge_isolated_uses_configured_mode(monkeypatch):
    monkeypatch.setenv("JUDGE_ISOLATION", "inline")
    runner.reset_runner()
    started = time.monotonic()
    result = runner.judge_isolated(r"1", r"1")
    assert result["verdict"] == "AC"
    assert time.monotonic() - started < 10
    assert isinstance(runner.get_runner(), runner.InlineRunner)
    runner.reset_runner()
