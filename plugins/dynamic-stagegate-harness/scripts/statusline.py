"""Claude Code statusline에 진행 중인 계획의 상태 경로를 그린다. 사용자가 고를 때만 켠다.

python -X utf8 statusline.py --check       켜져 있는지 출력한다
python -X utf8 statusline.py --install     이 파일을 설정 폴더에 복사하고 settings.json의 statusLine을 그 사본으로 바꾼다
python -X utf8 statusline.py --uninstall   statusLine을 켜기 전 명령으로 되돌린다
python -X utf8 statusline.py               (statusLine이 부르는 형태) stdin의 세션 JSON을 받아 줄을 출력한다

설정 폴더는 CLAUDE_CONFIG_DIR, 없으면 ~/.claude다. 플러그인 캐시 경로는 버전마다 바뀌어 statusLine에 적을 수 없으므로
설정 폴더의 고정 경로(stagegate-statusline.py)에 사본을 두고 그 경로를 적는다. 이미 다른 statusLine(claude-dashboard 같은)이
있으면 지우지 않고, 사본이 그 명령을 먼저 돌려 출력한 뒤 아래에 계획 줄을 붙인다. 앞 명령은 stagegate-statusline.json에 남긴다.
계획 줄은 작업 폴더의 .gate/gate.py status --line 출력이고, 하네스가 없는 폴더에서는 앞 명령의 출력만 나온다.
status --line이 실패하면(그 옵션이 없는 예전 엔진 따위) 상태 파일과 config.toml을 직접 읽어 같은 모양으로 그린다.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

NAME = "stagegate-statusline"


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def our_command(dest):
    return f'python -X utf8 "{dest.as_posix()}"'


def esc(code, s):
    return f"\x1b[{code}m{s}\x1b[0m"


def paint(line):
    """지난 상태는 초록, 건너뛴 상태는 회색, 지금 상태는 채운 칸, 끝나는 시각은 노랑"""
    stage = ""
    s = re.match(r"^(.*?) │ (.*)$", line)
    if s:
        stage, line = esc("1;38;5;183", s.group(1)) + esc("2", " │ "), s.group(2)
    m = re.match(r"^(P\d+ \S+)  (.*?)(?:  (\S+쯤 끝남))?$", line)
    if not m:
        return stage + line
    bar = [esc("1;38;5;16;48;5;222", f" {t[1:-1]} ") if t.startswith("[") else esc("38;5;114", t) if t.endswith(" ✓")
           else esc("38;5;246", t) if t.endswith(" 건너뜀") else t for t in m.group(2).split(" → ")]
    return (stage + esc("1", m.group(1)) + "  " + esc("38;5;246", " → ").join(bar)
            + (f"  {esc('1;38;5;222', m.group(3))}" if m.group(3) else ""))


def plan_lines(d):
    """status --line을 쓸 수 없을 때 상태 파일과 지금 구조로 그린 줄. 계획이 쓰는 구조 버전이 아니라 지금 구조의 경로를 쓴다"""
    cfg = tomllib.loads((Path(d) / ".gate" / "config.toml").read_text(encoding="utf-8"))
    flow = json.loads((Path(d) / cfg.get("project", {}).get("state_file", ".gate/state.json")).read_text(encoding="utf-8"))
    out = []
    for pid, p in flow.get("plans", {}).items():
        if p.get("state") in ("DONE", "ABANDONED"):
            continue
        order, visits, bar = cfg.get("kinds", {}).get(p.get("kind"), {}).get("states", []), p.get("visits", {}), []
        cur = order.index(p["state"]) if p.get("state") in order else len(order)
        for i, st in enumerate(order):
            if i == cur:
                bar.append(f"[{st} {visits[st]}회차]" if visits.get(st, 1) > 1 else f"[{st}]")
            elif i < cur and visits.get(st):
                bar.append(f"{st} ✓")
            else:
                bar.append(f"{st} 건너뜀" if i < cur and st in p.get("skipped", []) else st)
        eta = f"  {p['run']['eta']}쯤 끝남" if p.get("run") else ""
        out.append(f"{pid} {p.get('kind')}  " + " → ".join(bar + ["DONE"]) + eta)
    out = out or ["계획 없음"]
    stage = next((s for s in cfg.get("stages", []) if s.get("n") == flow.get("stage")), None)
    if stage:
        out[0] = f"{stage['n']}단계 {stage.get('name', '')}".rstrip() + " │ " + out[0]
    return out


def render():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        info = json.loads(raw)
    except ValueError:
        info = {}
    out = []
    conf = Path(__file__).with_suffix(".json")
    wrap = json.loads(conf.read_text(encoding="utf-8")).get("wrap") if conf.exists() else None
    if wrap:  # 켜기 전에 쓰던 statusline
        try:
            r = subprocess.run(wrap, shell=True, input=raw.encode("utf-8"), capture_output=True, timeout=8)
            out += [x for x in r.stdout.decode("utf-8", "replace").rstrip().split("\n") if x]
        except (OSError, subprocess.SubprocessError):
            pass
    ws = info.get("workspace") or {}
    d = ws.get("project_dir") or ws.get("current_dir") or info.get("cwd")
    if d and (Path(d) / ".gate" / "gate.py").is_file():
        lines = None
        try:
            r = subprocess.run([sys.executable, "-X", "utf8", ".gate/gate.py", "status", "--line"], cwd=d,
                               capture_output=True, timeout=3)
            if r.returncode == 0:
                lines = [x for x in r.stdout.decode("utf-8", "replace").strip().split("\n") if x]
        except (OSError, subprocess.SubprocessError):
            pass
        if lines is None:
            try:
                lines = plan_lines(d)
            except (OSError, ValueError, KeyError, TypeError):
                lines = ["stagegate 상태를 읽지 못했다(gate.py status --line 실패)"]
        out += [paint(x) for x in lines]
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))


def load_settings(f):
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_settings(f, settings):
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def main():
    if len(sys.argv) == 1:
        return render()
    cfg = config_dir()
    dest, conf, f = cfg / f"{NAME}.py", cfg / f"{NAME}.json", cfg / "settings.json"
    settings = load_settings(f)
    line = settings.get("statusLine") if isinstance(settings.get("statusLine"), dict) else {}
    on = line.get("command") == our_command(dest)
    arg = sys.argv[1]
    if arg == "--check":
        wrap = json.loads(conf.read_text(encoding="utf-8")).get("wrap") if on and conf.exists() else None
        print("켜짐" + (f" (앞 명령: {wrap})" if wrap else "") if on else
              "꺼짐" + (f" (지금 statusLine: {line['command']})" if line.get("command") else ""))
    elif arg == "--install":
        cfg.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(__file__, dest)
        if on:
            print(f"이미 켜져 있다. 사본만 새로 복사했다: {dest}")
            return
        conf.write_text(json.dumps({"wrap": line.get("command")}, ensure_ascii=False) + "\n", encoding="utf-8")
        if f.exists() and not f.with_name("settings.json.bak").exists():
            shutil.copyfile(f, f.with_name("settings.json.bak"))
        settings["statusLine"] = line | {"type": "command", "command": our_command(dest)}
        save_settings(f, settings)
        print(f"statusLine을 {dest}로 바꿨다." + (f" 앞 명령({line['command']})의 출력 아래에 계획 줄을 붙인다."
                                                if line.get("command") else "") + " 새 세션부터 보인다.")
    elif arg == "--uninstall":
        if not on:
            print("켜져 있지 않아 바꾸지 않았다.")
            return
        wrap = json.loads(conf.read_text(encoding="utf-8")).get("wrap") if conf.exists() else None
        if wrap:
            settings["statusLine"] = line | {"command": wrap}
        else:
            settings.pop("statusLine")
        save_settings(f, settings)
        for x in (dest, conf):
            x.unlink(missing_ok=True)
        print("statusLine을 " + (f"앞 명령({wrap})으로 되돌렸다." if wrap else "지웠다."))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
