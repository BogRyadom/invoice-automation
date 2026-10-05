import threading

from app.worker import run


def test_run_returns_immediately_when_already_stopped() -> None:
    stop = threading.Event()
    stop.set()

    assert run(stop, poll_interval=10) == 0


def test_run_stops_when_signalled() -> None:
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.start()

    iterations = run(stop, poll_interval=0.01)
    timer.join()

    assert iterations >= 1
