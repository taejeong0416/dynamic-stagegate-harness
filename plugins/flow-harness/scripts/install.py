"""프로젝트에 flow 하네스를 설치하거나 엔진을 갱신한다.

python -X utf8 install.py <preset> [--target 프로젝트 폴더]   새로 설치(preset: ml, agent, service)
python -X utf8 install.py --update [--target 프로젝트 폴더]   엔진(.flow/flowkit)과 git hook만 다시 복사

설치는 엔진, preset의 config.toml, 계획 틀, FLOW.md, git hook을 복사하고 core.hooksPath를 .githooks로 맞춘다.
이미 있는 config.toml, 틀, FLOW.md는 덮어쓰지 않는다. 커밋은 하지 않는다.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PRESETS = HERE / "presets"
SHIM = '''"""flow 명령 진입점. 프로젝트 루트에서 python -X utf8 .flow/flow.py <명령>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flowkit.cli import main  # noqa: E402

main()
'''
GITHOOKS = {
    "pre-commit": "#!/bin/sh\n# flow 하네스: 커밋할 코드 파일이 코드 상태 계획에 있는지 검사한다\n"
                  "cd \"$(git rev-parse --show-toplevel)\" || exit 1\npython -X utf8 .flow/flow.py git-pre-commit\n",
    "commit-msg": "#!/bin/sh\n# flow 하네스: 하네스 파일 커밋을 따로 두고 접두어를 검사한다\n"
                  "cd \"$(git rev-parse --show-toplevel)\" || exit 1\npython -X utf8 .flow/flow.py git-commit-msg \"$1\"\n",
}
MARK = "# flow 하네스:"


def copy_engine(flow_dir):
    dst = flow_dir / "flowkit"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(HERE / "flowkit", dst, ignore=shutil.ignore_patterns("__pycache__"))
    (flow_dir / "flow.py").write_text(SHIM, encoding="utf-8")


def write_githooks(target):
    d = target / ".githooks"
    d.mkdir(exist_ok=True)
    for name, body in GITHOOKS.items():
        f = d / name
        if f.exists() and MARK not in f.read_text(encoding="utf-8"):
            sys.exit(f"{f}가 이미 있고 flow 하네스 hook이 아니다. 내용을 합친 뒤 다시 실행한다")
        f.write_text(body, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    cur = subprocess.run(["git", "config", "core.hooksPath"], cwd=target, capture_output=True, text=True).stdout.strip()
    if cur and cur != ".githooks":
        sys.exit(f"core.hooksPath가 이미 {cur}다. .githooks로 옮긴 뒤 다시 실행한다")
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=target, check=True)


def copy_new(src, dst):
    if dst.exists():
        print(f"  그대로 둠: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    print(f"  복사: {dst}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", nargs="?")
    ap.add_argument("--target", default=".")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    target = Path(a.target).resolve()
    if subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True).returncode != 0:
        sys.exit(f"{target}는 git 저장소가 아니다. git init을 먼저 한다")
    flow_dir = target / ".flow"
    if a.update:
        if not (flow_dir / "config.toml").exists():
            sys.exit("설치된 하네스가 없다. preset 이름을 주고 새로 설치한다")
        copy_engine(flow_dir)
        write_githooks(target)
        print(f"엔진과 git hook을 갱신했다. 하네스 커밋으로 남긴다: git commit -m \"harness: flowkit 갱신\"")
        return
    names = sorted(p.name for p in PRESETS.iterdir() if p.is_dir())
    if a.preset not in names:
        sys.exit(f"preset은 {', '.join(names)} 중 하나다")
    src = PRESETS / a.preset
    flow_dir.mkdir(exist_ok=True)
    copy_engine(flow_dir)
    for name in ("config.toml", "FLOW.md", "templates"):
        copy_new(src / name, flow_dir / name)
    cfg = tomllib.loads((flow_dir / "config.toml").read_text(encoding="utf-8"))
    proj = cfg.get("project", {})
    state = target / proj.get("state_file", ".flow/state.json")
    if not state.exists():
        stages = cfg.get("stages", [])
        state.write_text(json.dumps({"stage": stages[0]["n"] if stages else None, "plans": {}}, indent=2) + "\n",
                         encoding="utf-8")
    (target / proj.get("plans_dir", "plans")).mkdir(parents=True, exist_ok=True)
    write_githooks(target)
    print(f"\n{a.preset} preset을 설치했다. 다음 순서로 마무리한다.")
    print("  1. .flow/config.toml의 [project] code, tracked 패턴을 이 프로젝트 폴더에 맞춘다")
    print("  2. 하네스 파일만 커밋한다: git add .flow .githooks, git reset .flow/state.json, "
          "git commit -m \"harness: flow 하네스 설치\"")
    print("  3. 상태 파일을 따로 커밋한다: git add .flow/state.json, git commit -m \"chore: flow 상태 파일\"")
    print("  4. python -X utf8 .flow/flow.py status")


if __name__ == "__main__":
    main()
