"""运行 V0.53 固定离线评测集；CI 与人工验收使用同一入口。"""

from __future__ import annotations

import subprocess
import sys


if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "-q", "tests/test_assessment_eval_cases.py"]
        )
    )
