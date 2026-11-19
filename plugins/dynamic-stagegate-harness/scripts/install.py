"""프로젝트에 stagegate 하네스를 설치하거나 엔진을 갱신한다.

python -X utf8 install.py --answer work=service --answer tests=yes [--local 파일] [--dry-run] [--target 프로젝트 폴더]
python -X utf8 install.py --update [--target 프로젝트 폴더]   엔진(.gate/gatekit), 라이브러리, git hook만 다시 복사

설치는 고정 질문의 답과 저장소의 사실로 라이브러리 부품의 적용 조건(applies_when)을 판정하고, 조건이 맞은 부품을
.gate/parts/에 복사해 .gate/config.toml로 조립한다. 답과 덮어쓰기는 .gate/local.toml에, 부품의 버전과 해시는
.gate/parts.lock에 남는다. --local은 [answers]와 덮어쓰기 표([project] code 같은)를 미리 적은 파일이다.
--dry-run은 고른 부품과 뺀 부품만 보이고 아무것도 쓰지 않는다. 이미 있는 config.toml은 덮어쓰지 않는다.
갱신은 엔진, GATE.md, 라이브러리, git hook만 바꾼다. 커밋은 하지 않는다.
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
sys.path.insert(0, str(HERE))
from gatekit import parts as PT  # noqa: E402

LIBRARY = HERE / "library"
SHIM = '''"""gate 명령 진입점. 프로젝트 루트에서 python -X utf8 .gate/gate.py <명령>"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 엔진 폴더에 __pycache__를 남기지 않는다
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
LOCAL_HEAD = ("# 이 프로젝트의 입력. [answers]는 부품을 고르는 고정 질문의 답이고, 나머지 표는 조립 결과를 덮어쓴다"
              "(예: [project] code).\n# 제안서와 gate adopt로만 바꾼다(.gate/GATE.md).\n")


def copy_engine(gate_dir):
    dst = gate_dir / "gatekit"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(HERE / "gatekit", dst, ignore=shutil.ignore_patterns("__pycache__"))
    (gate_dir / "gate.py").write_text(SHIM, encoding="utf-8")
    shutil.copy2(HERE / "GATE.md", gate_dir / "GATE.md")
    lib = gate_dir / "library"  # 부품 라이브러리. 구조를 바꿀 때 여기서 부품을 가져온다
    if lib.exists():
        shutil.rmtree(lib)
    shutil.copytree(LIBRARY, lib)


def engine_hash(d):
    """gatekit/*.py 내용의 해시. gatekit.core.engine_version과 같은 계산이다"""
    import hashlib
    h = hashlib.sha256()
    for f in sorted(Path(d).glob("*.py")):
        h.update(f.name.encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def open_plans(gate_dir):
    """진행 중인 계획 번호. 엔진을 바꾸기 전에 알리기 위해서다"""
    try:
        proj = tomllib.loads((gate_dir / "config.toml").read_text(encoding="utf-8")).get("project", {})
        state = json.loads((gate_dir.parent / proj.get("state_file", ".gate/state.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [pid for pid, p in state.get("plans", {}).items() if p.get("state") not in ("DONE", "ABANDONED")]


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


def ignored(target, *paths):
    """paths 가운데 .gitignore에 걸리는 것"""
    r = subprocess.run(["git", "check-ignore", "--", *paths], cwd=target, capture_output=True, text=True, encoding="utf-8")
    return set(r.stdout.split())


def read_inputs(a):
    """(덮어쓰기, 답). --local 파일에 --answer를 더한다. 답의 쉼표는 여러 답이다"""
    local = tomllib.loads(Path(a.local).read_text(encoding="utf-8")) if a.local else {}
    answers = dict(local.pop("answers", {}))
    for item in a.answer:
        key, _, val = item.partition("=")
        if not val:
            sys.exit(f"--answer는 질문=답으로 쓴다(지금 {item})")
        answers[key.strip()] = val.split(",") if "," in val else val
    return local, answers


def local_text(local, answers):
    out = LOCAL_HEAD + "\n[answers]\n" + "".join(PT._line(k, v) + "\n" for k, v in answers.items())
    return out + ("\n" + PT.dumps(local) if local else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", action="append", default=[])
    ap.add_argument("--local")
    ap.add_argument("--dry-run", action="store_true")
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
            sys.exit("설치된 하네스가 없다. --answer로 답을 주고 새로 설치한다")
        before = engine_hash(gate_dir / "gatekit")
        copy_engine(gate_dir)
        write_githooks(target)
        busy = open_plans(gate_dir)
        if busy and engine_hash(gate_dir / "gatekit") != before:
            print(f"경고: 진행 중인 계획 {', '.join(busy)}는 구조 버전은 그대로 쓰지만 검사 코드가 바뀐 엔진으로 넘어간다. "
                  "status와 SessionStart가 '엔진 바뀜'으로 알린다. 가능하면 이 계획들을 끝낸 뒤 갱신한다.")
        engine = [".gate/gatekit", ".gate/gate.py", ".gate/GATE.md", ".gate/library", ".githooks"]
        keep = [x for x in engine if x not in ignored(target, *engine)]
        print("엔진, GATE.md, 라이브러리, git hook을 갱신했다. " + (
            f"하네스 커밋으로 남긴다: git add {' '.join(keep)}, git commit -m \"harness: gatekit 갱신\"" if keep else
            "엔진 복사본을 git이 추적하지 않아 커밋할 것이 없다."))
        if (gate_dir / "parts").exists():
            print("라이브러리에서 바뀐 부품은 python -X utf8 .gate/gate.py parts로 본다.")
        return
    local, answers = read_inputs(a)
    on, off = PT.select(PT.load_parts(LIBRARY), target, answers)
    print("조건이 맞은 부품")
    for p in on:
        print(f"  {p['name']}: {p.get('description', '')} [{PT.fmt_cond(p['applies_when'])}]")
    print("뺀 부품")
    for p in off:
        print(f"  {p['name']}: {p.get('description', '')} [{PT.fmt_cond(p.get('applies_when', {})) or '조건 없음'}]")
    if not on:
        sys.exit("조건이 맞은 부품이 없다. --answer work=service처럼 고정 질문의 답을 준다(질문 목록은 setup skill에 있다)")
    cfg, bad = PT.assemble(on, local)
    if bad:
        sys.exit("이 부품들은 함께 조립되지 않는다.\n" + "\n".join(f"  {b}" for b in bad))
    if a.dry_run:
        return
    gate_dir.mkdir(exist_ok=True)
    copy_engine(gate_dir)
    if (gate_dir / "config.toml").exists():
        print(f"  그대로 둠: {gate_dir / 'config.toml'}")
    else:
        for p in on:
            shutil.copytree(LIBRARY / p["name"], gate_dir / "parts" / p["name"])
        (gate_dir / "local.toml").write_text(local_text(local, answers), encoding="utf-8")
        (gate_dir / "config.toml").write_text(PT.dumps(cfg, PT.HEAD), encoding="utf-8")
        PT.write_lock(gate_dir, PT.load_parts(gate_dir / "parts"))
    cfg = tomllib.loads((gate_dir / "config.toml").read_text(encoding="utf-8"))
    proj = cfg.get("project", {})
    state = target / proj.get("state_file", ".gate/state.json")
    if not state.exists():
        stages = cfg.get("stages", [])
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=target, capture_output=True,
                              text=True).stdout.strip()
        harness = {"version": cfg.get("version", 1), "adopted": "설치", "at": time.strftime("%Y-%m-%dT%H:%M"),
                   "ts": time.time(), "commit": head, "stage": stages[0]["n"] if stages else None}
        state.write_text(json.dumps({"stage": harness["stage"], "plans": {}, "harness": harness}, ensure_ascii=False,
                                    indent=2) + "\n", encoding="utf-8")
    (target / proj.get("plans_dir", "plans")).mkdir(parents=True, exist_ok=True)
    write_githooks(target)
    print("\n하네스를 설치했다. 다음 순서로 마무리한다.")
    print("  1. 하네스 파일만 커밋한다: git add .gate .githooks, git reset .gate/state.json, "
          "git commit -m \"harness: stagegate 하네스 설치\"")
    rel = state.relative_to(target).as_posix()
    keep_state = rel not in ignored(target, rel)
    if keep_state:
        print(f"  2. 상태 파일을 따로 커밋한다: git add {rel}, git commit -m \"chore: gate 상태 파일\"")
    print(f"  {3 if keep_state else 2}. python -X utf8 .gate/gate.py describe로 구조를 보고, setup skill의 인터뷰로 "
          "PROFILE.md와 첫 조정을 한다")


if __name__ == "__main__":
    main()
