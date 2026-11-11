from concurrent.futures import ThreadPoolExecutor

from utils.history_manager import HistoryManager


def test_history_add_record_is_transactional_across_writers(tmp_path):
    manager = HistoryManager(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: manager.add_record({"id": index}), range(40)))

    history = manager.load_history()
    assert len(history) == 40
    assert {item["id"] for item in history} == set(range(40))
