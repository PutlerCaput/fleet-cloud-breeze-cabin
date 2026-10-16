import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from finam_robot import kernels  # noqa: E402

kernels.warmup()
