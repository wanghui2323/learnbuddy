"""SM-2 间隔重复调度单测（Batch 2C+ · #2）。纯函数，无 IO。"""

from core.scheduler import grade, sm2


def test_grade_mapping():
    assert grade("cant") < 3
    assert grade("with_help") == 3
    assert grade("independent") == 5
    assert grade(None) == 3


def test_single_fail_one_day():
    s = sm2(["cant"])
    assert s["interval"] == 1 and s["reps"] == 0


def test_passing_grows_interval():
    assert sm2(["independent"])["interval"] == 1               # 第 1 次
    assert sm2(["independent", "independent"])["interval"] == 6  # 第 2 次
    s3 = sm2(["independent"] * 3)
    assert s3["interval"] > 6 and s3["reps"] == 3              # 第 3 次起按 ease 拉长


def test_fail_resets():
    s = sm2(["independent", "independent", "independent", "cant"])
    assert s["interval"] == 1 and s["reps"] == 0              # 答错 → 重置重学


def test_easy_outpaces_hard():
    easy = sm2(["independent"] * 4)
    hard = sm2(["with_help"] * 4)
    assert easy["interval"] > hard["interval"]                # 答得轻松 → 间隔拉长更快
    assert easy["ease"] > hard["ease"]
