"""ExecutionObservations 子观察读取的回归测试。"""

from factor_demo.execution_observations import ExecutionObservations, parent_execution


def test_children_reads_direct_children_in_creation_order(tmp_path):
    """children 应通过持久化服务读取，而不是调用已删除的连接方法。"""
    journal = ExecutionObservations(tmp_path)
    parent = journal.save(
        {
            "observation_id": "parent",
            "kind": "execution",
            "status": "passed",
            "parent_id": None,
        }
    )
    token = parent_execution.set(parent)
    try:
        first = journal.record("execution", "passed")
        second = journal.record("execution", "failed")
    finally:
        parent_execution.reset(token)

    # 孙观察不属于直接子观察，不应出现在 children 结果里。
    token = parent_execution.set(first)
    try:
        journal.record("execution", "failed")
    finally:
        parent_execution.reset(token)

    unrelated = journal.save(
        {
            "observation_id": "unrelated",
            "kind": "execution",
            "status": "passed",
            "parent_id": None,
        }
    )

    children = journal.children(parent)
    assert [doc["observation_id"] for doc in children] == [first, second]

    # 新实例只能从数据库读回，覆盖旧实现调用已删除 connect 的路径。
    restored = ExecutionObservations(tmp_path).children(parent)
    assert [doc["observation_id"] for doc in restored] == [first, second]
    assert journal.children(unrelated) == []
