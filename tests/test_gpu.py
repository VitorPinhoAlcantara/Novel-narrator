from novel_narrator.tts.gpu import decide_workers

TTS = {
    "workers": 3,
    "gpu_util_low": 35,
    "gpu_util_moderate": 70,
    "vram_per_worker_mb": 3500,
    "vram_reserve_mb": 2000,
}
TOTAL = 16311


def test_low_usage_and_free_memory_uses_three():
    assert decide_workers(5, 3000, TOTAL, 0, TTS) == 3


def test_moderate_usage_uses_two():
    assert decide_workers(50, 3000, TOTAL, 0, TTS) == 2


def test_high_usage_uses_one():
    assert decide_workers(90, 3000, TOTAL, 0, TTS) == 1


def test_low_memory_limits_workers():
    assert decide_workers(5, 8000, TOTAL, 0, TTS) == 1
    assert decide_workers(5, 6000, TOTAL, 0, TTS) == 2


def test_own_workers_memory_is_not_external():
    assert decide_workers(5, 3000 + 3 * 3500, TOTAL, 3, TTS) == 3


def test_never_less_than_one():
    assert decide_workers(100, 16000, TOTAL, 0, TTS) == 1
