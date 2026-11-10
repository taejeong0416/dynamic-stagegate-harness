"""프로젝트에 stagegate 하네스를 설치하거나 엔진을 갱신한다.

python -X utf8 install.py <preset> [--target 프로젝트 폴더]   새로 설치(preset: ml, agent, service)
python -X utf8 install.py --update [--target 프로젝트 폴더]   엔진(.gate/gatekit)과 git hook만 다시 복사

설치는 엔진, preset의 config.toml, GATE.md, 블록 라이브러리(모든 preset의 config, .gate/library/), git hook을 복사하고
core.hooksPath를 .githooks로 맞춘다. 이미 있는 config.toml은 덮어쓰지 않는다. 갱신은 엔진, GATE.md, 라이브러리,
git hook만 바꾼다. 커밋은 하지 않는다.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PRESETS = HERE / "presets"
SHIM = '''"""gate 명령 진입점. 프로젝트 루트에서 python -X utf8 .gate/gate.py <명령>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gatekit.cli import main  # noqa: E402

main()
'''
GITHOOKS = {
    "pre-commit": "#!/bin/sh\n# stagegate 하네스: 커밋할 코드 파일이 코드 상태 계획에 있는지 검사한다\n"
                  "cd \"$(git rev-parse --show-toplevel)\" || exit 1\npython -X utf8 .gate/gate.py git-pre-commit\n",
    "commit-msg": "#!/bin/sh\n# stagegate 하네스: 하네스 파일 커밋을 따로 두고 접두어를 검사한다\n"
                  "cd \"$(git rev-parse --show-toplevel)\" || exit 1\npython -X utf8 .gate/gate.py git-commit-msg \"$1\"\n",
}
MARK = "# stagegate 하네스:"


def copy_engine(gate_dir):
    dst = gate_dir / "gatekit"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(HERE / "gatekit", dst, ignore=shutil.ignore_patterns("__pycache__"))
    (gate_dir / "gate.py").write_text(SHIM, encoding="utf-8")
    shutil.copy2(HERE / "GATE.md", gate_dir / "GATE.md")
    lib = gate_dir / "library"  # 구조를 짤 때 빌려 쓸 상태와 절의 모음
    lib.mkdir(exist_ok=True)
    for p in PRESETS.iterdir():
        if (p / "config.toml").exists():
            shutil.copy2(p / "config.toml", lib / f"{p.name}.toml")


def check_githooks(target):
    """git hook을 쓸 수 없는 저장소면 아무것도 복사하기 전에 멈춘다"""
    for name in GITHOOKS:
        f = target / ".githooks" / name
        if f.exists() and MARK not in f.read_text(encoding="utf-8"):
            sys.exit(f"{f}가 이미 있고 stagegate 하네스 hook이 아니다. 내용을 합친 뒤 다시 실행한다")
    cur = subprocess.run(["git", "config", "core.hooksPath"], cwd=target, capture_output=True, text=True).stdout.strip()
    if cur and cur != ".githooks":
        sys.exit(f"core.hooksPath가 이미 {cur}다. .githooks로 옮긴 뒤 다시 실행한다")


def write_githooks(target):
    d = target / ".githooks"
    d.mkdir(exist_ok=True)
    for name, body in GITHOOKS.items():
        f = d / name
        f.write_text(body, encoding="utf-8", newline="\n")
        f.chmod(0o755)
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
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True,
                         encoding="utf-8")
    if top.returncode != 0:
        sys.exit(f"{target}는 git 저장소가 아니다. git init을 먼저 한다")
    if not os.path.samefile(top.stdout.strip(), target):
        sys.exit(f"{target}는 git 저장소의 루트가 아니다. 엔진과 git hook이 저장소 루트를 기준으로 돌아서 "
                 f"{top.stdout.strip()}에 설치한다")
    check_githooks(target)
    gate_dir = target / ".gate"
    if a.update:
        if not (gate_dir / "config.toml").exists():
            sys.exit("설치된 하네스가 없다. preset 이름을 주고 새로 설치한다")
        copy_engine(gate_dir)
        write_githooks(target)
        print("엔진, GATE.md, 라이브러리, git hook을 갱신했다. 하네스 커밋으로 남긴다: "
              "git add .gate/gatekit .gate/gate.py .gate/GATE.md .gate/library .githooks, git commit -m \"harness: gatekit 갱신\"")
        return
    names = sorted(p.name for p in PRESETS.iterdir() if p.is_dir())
    if a.preset not in names:
        sys.exit(f"preset은 {', '.join(names)} 중 하나다")
    src = PRESETS / a.preset
    gate_dir.mkdir(exist_ok=True)
    copy_engine(gate_dir)
    copy_new(src / "config.toml", gate_dir / "config.toml")
    cfg = tomllib.loads((gate_dir / "config.toml").read_text(encoding="utf-8"))
    proj = cfg.get("project", {})
    state = target / proj.get("state_file", ".gate/state.json")
    if not state.exists():
        stages = cfg.get("stages", [])
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=target, capture_output=True,
                              text=True).stdout.strip()
        harness = {"version": cfg.get("version", 1), "adopted": f"설치({a.preset})", "at": time.strftime("%Y-%m-%dT%H:%M"),
                   "ts": time.time(), "commit": head, "stage": stages[0]["n"] if stages else None}
        state.write_text(json.dumps({"stage": harness["stage"], "plans": {}, "harness": harness}, ensure_ascii=False,
                                    indent=2) + "\n", encoding="utf-8")
    (target / proj.get("plans_dir", "plans")).mkdir(parents=True, exist_ok=True)
    write_githooks(target)
    print(f"\n{a.preset} preset을 설치했다. 다음 순서로 마무리한다.")
    print("  1. .gate/config.toml의 [project] code, tracked 패턴을 이 프로젝트 폴더에 맞춘다")
    print("  2. 하네스 파일만 커밋한다: git add .gate .githooks, git reset .gate/state.json, "
          "git commit -m \"harness: stagegate 하네스 설치\"")
    print("  3. 상태 파일을 따로 커밋한다: git add .gate/state.json, git commit -m \"chore: gate 상태 파일\"")
    print("  4. python -X utf8 .gate/gate.py describe로 구조를 보고, setup skill의 인터뷰로 PROFILE.md와 첫 조정을 한다")


if __name__ == "__main__":
    main()
