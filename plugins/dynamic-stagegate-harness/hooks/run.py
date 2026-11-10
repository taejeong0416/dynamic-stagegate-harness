"""플러그인 hook 진입점. 프로젝트에 .gate/config.toml이 있을 때만 그 프로젝트의 엔진(.gate/gatekit)을 부른다.

설치하지 않은 프로젝트에서는 아무것도 하지 않는다. 검토자 프로세스(GATE_REVIEW)에서도 돌지 않는다.
"""
import os
import sys
from pathlib import Path

if os.environ.get("GATE_REVIEW"):
    sys.exit(0)
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
if not (root / ".gate" / "config.toml").is_file() or not (root / ".gate" / "gatekit").is_dir():
    sys.exit(0)
sys.path.insert(0, str(root / ".gate"))
from gatekit.hooks import main  # noqa: E402

main(sys.argv[1], root)
