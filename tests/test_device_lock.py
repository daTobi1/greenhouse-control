import threading

from services import device_lock


def test_same_index_returns_same_lock():
    assert device_lock.get(0) is device_lock.get(0)


def test_different_index_returns_different_lock():
    assert device_lock.get(0) is not device_lock.get(1)


def test_concurrent_creation_yields_one_lock():
    """Zwei Threads dürfen für denselben Index nicht zwei Locks erzeugen."""
    results = []
    barrier = threading.Barrier(2)

    def grab():
        barrier.wait()
        results.append(device_lock.get(99))

    threads = [threading.Thread(target=grab) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0] is results[1]


def test_lock_is_usable():
    lock = device_lock.get(42)
    assert lock.acquire(timeout=1)
    try:
        assert not lock.acquire(blocking=False)
    finally:
        lock.release()
