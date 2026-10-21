"""steward 的单测包。

**真实账本一次都不该被打开**：模块导入前把 SHADOWROOM_STEWARD_DB 指到临时目录，
每个测试再在 setUp 里换成自己的一次性库（见 base.StewardTestCase）。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 永远覆盖：不继承调用者的环境变量，免得有人不小心往真账本里写测试数据。
os.environ["SHADOWROOM_STEWARD_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="steward-tests-"), "ledger.db"
)
