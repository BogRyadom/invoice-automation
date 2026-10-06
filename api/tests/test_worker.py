import threading

import pytest

from app import worker
from app.config import Settings
from app.processing import ProviderFatal
from tests.fakes import ScriptedProvider


def test_run_returns_immediately_when_already_stopped(settings: Settings) -> None:
    stop = threading.Event()
    stop.set()

    assert worker.run(stop, None, None, ScriptedProvider([]), settings) == 0


def test_run_processes_until_stopped(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    stop = threading.Event()
    outcomes = [True, True, False]

    def fake_run_once(*_: object) -> bool:
        busy = outcomes.pop(0)
        if not outcomes:
            stop.set()
        return busy

    monkeypatch.setattr(worker, "run_once", fake_run_once)

    assert worker.run(stop, None, None, ScriptedProvider([]), settings) == 2


def test_run_pauses_when_the_provider_is_unusable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    stop = threading.Event()
    waits: list[float] = []

    def fake_run_once(*_: object) -> bool:
        raise ProviderFatal("daily quota")

    def fake_wait(seconds: float) -> bool:
        waits.append(seconds)
        stop.set()
        return True

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(stop, "wait", fake_wait)

    assert worker.run(stop, None, None, ScriptedProvider([]), settings) == 0
    assert waits == [worker.PROVIDER_FATAL_PAUSE_SECONDS]
