"""설정, 구조 버전, 상태 파일, 계획 파일 파싱, git 도우미.

이 패키지는 프로젝트의 .gate/gatekit/에 복사되어 돈다. 프로젝트 루트는 .gate/의 부모다.
구조(종류, 상태, 절, 조건부 경로)는 .gate/config.toml에 있고 version으로 센다. 바뀔 때마다 앞 버전은
.gate/history/v<N>.toml에 남고, 계획은 시작할 때의 버전을 끝까지 쓴다.
"""
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import time
import tokenize
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE_DIR = ROOT / ".gate"
CLOSED = ("DONE", "ABANDONED")

DEFAULTS = {
    "plans_dir": "plans",
    "state_file": ".gate/state.json",
    "plan_prefix": "P",
    "code": [],
    "tracked": [],
    "harness": [".gate/gatekit/", ".gate/gate.py", ".gate/config.toml", ".gate/history/", ".gate/library/",
                ".gate/GATE.md", ".githooks/"],
    # 에이전트가 편집 도구나 셸로 직접 쓰지 못하는 파일. 구조는 제안서와 gate adopt로만 바꾼다
    "protected": [".gate/config.toml", ".gate/history/", ".gate/gatekit/", ".gate/gate.py", ".githooks/"],
    "harness_prefix": "harness:",
    "max_visits": 3,
    "files_section": "변경 파일",
    "unreviewed": [],
    "stop_states": [],
    "decisions_file": "",
    "status_file": "",
    "status_sections": [],
    "pre_commit": [],
    "test": "",
    # 계획의 코드 파일이 영향을 주는 테스트만 돌리는 명령. {files}는 계획의 코드 파일로 바뀐다
    "affected": "",
    # 테스트 파일 패턴. test_edits 검사와 먼저 실패한 테스트 기록이 쓴다
    "tests": [],
    # 하네스 점검 권장 기준. 근거 없이 정한 값
    "review_every_plans": 5,
    "review_every_events": 10,
}


def parse_config(text):
    raw = tomllib.loads(text)
    return {"version": raw.get("version", 1), "project": DEFAULTS | raw.get("project", {}),
            "kinds": raw.get("kinds", {}), "states": raw.get("states", {}), "sections": raw.get("sections", {}),
            "stages": raw.get("stages", []), "remote": raw.get("remote", {}), "review": raw.get("review", {})}


CFG = parse_config((GATE_DIR / "config.toml").read_text(encoding="utf-8"))
P = CFG["project"]
_versions = {CFG["version"]: CFG}


def cfg_for(p=None):
    """계획이 시작할 때의 구조. 계획이 없으면 지금 구조"""
    v = (p or {}).get("version", CFG["version"])
    if v not in _versions:
        f = GATE_DIR / "history" / f"v{v}.toml"
        _versions[v] = parse_config(f.read_text(encoding="utf-8")) if f.exists() else CFG
    return _versions[v]


def kind_states(cfg, kind):
    return cfg["kinds"][kind]["states"]


def template_text(cfg, kind):
    """종류의 계획 틀. 머리 bullet 뒤에, 종류의 상태들이 쓰는 절을 상태 순서대로 한 번씩 놓는다"""
    k = cfg["kinds"][kind]
    names = []
    for s in k["states"]:
        for sec in cfg["states"][s].get("sections", []):
            if sec not in names:
                names.append(sec)
    for sec in k.get("extra_sections", []):
        if sec not in names:
            names.append(sec)
    out = ["# P000 제목", "", "- 종류:", "- 선택:", "- 이어진 계획:", ""]
    for sec in names:
        b = cfg["sections"].get(sec, {}).get("body", "").strip("\n")
        out += [f"## {sec}", ""] + ([b, ""] if b else [])
    return "\n".join(out)


# 조건부 경로

def cond_holds(cond, p, s, cfg):
    """상태에 들를지 정하는 조건. 키를 모두 만족해야 한다.
    option: start --with로 켠 선택, changed: 계획 시작 뒤 바뀐 파일이 패턴에 맞음(작업 트리 포함),
    max_files: 계획이 적은 코드 파일 수가 n 이하, not: 아래 조건의 부정, any: 아래 조건 중 하나"""
    for key, v in cond.items():
        if key == "option":
            ok = v in p.get("options", [])
        elif key == "changed":
            since = p.get("entered", {}).get("_start", {}).get("commit") or head()
            files = changed_since(since) | {x for _, x in dirty()}
            ok = any(match(f, v) for f in files)
        elif key == "max_files":
            ok = len(plan_files(s)) <= v
        elif key == "not":
            ok = not cond_holds(v, p, s, cfg)
        elif key == "any":
            ok = any(cond_holds(c, p, s, cfg) for c in v)
        else:
            raise SystemExit(f"config.toml: 알 수 없는 조건 키 '{key}'")
        if not ok:
            return False
    return True


def next_state(cfg, p, s, after):
    """after 다음에 들를 상태. 조건이 맞지 않는 상태는 건너뛴다. (상태, 건너뛴 상태 목록)"""
    order = kind_states(cfg, p["kind"])
    when = cfg["kinds"][p["kind"]].get("when", {})
    skipped = []
    for st in order[order.index(after) + 1 if after else 0:]:
        if st in when and not cond_holds(when[st], p, s, cfg):
            skipped.append(st)
            continue
        return st, skipped
    return "DONE", skipped


# 이벤트 기록(하네스 점검 자료). git 폴더에 두어 커밋되지 않는다

def log_event(kind, reason, **extra):
    try:
        with open(git_path("gate-events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), "ts": time.time(), "kind": kind, "reason": reason[:200], **extra},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_events(since=0):
    f = git_path("gate-events.jsonl")
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("ts", 0) >= since:
            out.append(e)
    return out


STATE = ROOT / P["state_file"]
PLANS = ROOT / P["plans_dir"]


def match(path, globs):
    """fnmatch의 *는 /도 넘는다. /로 끝나는 패턴은 그 폴더 아래 전부"""
    return any(path.startswith(g) if g.endswith("/") else fnmatch.fnmatch(path, g) for g in globs)


def is_harness(path):
    return match(path, P["harness"])


def is_code(path):
    return match(path, P["code"]) and not is_harness(path)


def is_protected(path):
    return match(path, P["protected"])


def is_test(path):
    return match(path, P["tests"])


# git

def git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8").stdout


def head():
    return git("rev-parse", "--short", "HEAD").strip()


def changed_since(commit, *paths):
    out = git("diff", "--name-only", f"{commit}..HEAD", "--", *paths)
    return set(out.splitlines())


def dirty(*paths):
    """커밋하지 않은 변경. (상태, 경로) 목록"""
    out = []
    for line in git("status", "--porcelain", "--untracked-files=all", "--", *paths).splitlines():
        if len(line) > 3:
            out.append((line[:2], line[3:].split(" -> ")[-1].strip('"')))
    return out


def dirty_code():
    return [x for x in dirty() if is_code(x[1])]


def git_path(name):
    return ROOT / git("rev-parse", "--git-path", name).strip()


# 상태 파일

def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"stage": None, "plans": {}}


def save(flow):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def first(p, state):
    """그 상태에 처음 들어간 기록. back으로 다시 들어와도 검사 범위를 처음부터 잡는다"""
    return p.get("first", {}).get(state, p["entered"][state])


def state_def(p):
    return cfg_for(p)["states"].get(p["state"], {})


def code_open(flow):
    return any(state_def(p).get("code") for p in flow["plans"].values() if p["state"] not in CLOSED)


def tests_only(flow, path):
    """코드 상태인 계획이 모두 테스트만 쓰는 상태(edit = "tests")이고 path가 테스트 파일이 아니면 막는 이유"""
    plans = {pid: p for pid, p in flow["plans"].items() if p["state"] not in CLOSED and state_def(p).get("code")}
    if not plans or is_test(path) or any(state_def(p).get("edit") != "tests" for p in plans.values()):
        return None
    where = ", ".join(f"{pid} {p['state']}" for pid, p in plans.items())
    return (f"코드를 고치는 계획이 테스트만 쓰는 상태({where})라 테스트 파일이 아닌 {path}를 막았다. "
            "구현 코드는 next로 다음 상태에 간 뒤에 쓴다.")


def run_open(flow):
    return any(state_def(p).get("run") and p.get("run") for p in flow["plans"].values() if p["state"] not in CLOSED)


# 계획 파일

def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^## (.+)", line)
        if m:
            cur = m.group(1).strip()
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line)
    return out


def headings(text):
    return [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]


def tables(lines):
    """절 안의 표들. 표마다 데이터 행의 칸 목록"""
    out, cur = [], None
    for line in list(lines) + [""]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cur is None:
                cur = []
            elif not all(re.fullmatch(r":?-+:?", c) for c in cells):
                cur.append(cells)
        elif cur is not None:
            out.append(cur)
            cur = None
    return out


def bullets(lines):
    out = {}
    for line in lines:
        m = re.match(r"^- ([^:]+):\s*(.*)", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def filled(rows):
    return [r for r in rows if r and all(r)]


def body(lines):
    """주석과 빈 줄을 뺀 내용"""
    text = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.S)
    return [l for l in text.splitlines() if l.strip()]


def structure_problems(p, text):
    """절 제목이 틀과 같은 순서로 한 번씩 있고, 틀의 bullet 항목이 모두 있는지"""
    tpl = template_text(cfg_for(p), p["kind"])
    want, got = headings(tpl), headings(text)
    miss = []
    if got != want:
        miss.append(f"절 제목이 틀과 다르다(중복, 순서, 누락). 틀: {', '.join(want)}")
    ts, ss = sections(tpl), sections(text)
    for sec, lines in ts.items():
        for k in bullets(lines):
            if k not in bullets(ss.get(sec, [])):
                miss.append(f"'{sec}' 절의 '{k}' 항목이 없다")
    return miss


def plan_hash(text, cfg):
    """검토한 내용의 해시. 검토 뒤에 채우는 절(계획이 쓰는 구조 버전의 unreviewed)을 뺀다"""
    parts = [text.split("\n## ")[0]]
    for name, lines in sections(text).items():
        if name not in cfg["project"]["unreviewed"]:
            parts.append(name + "\n" + "\n".join(lines))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def snapshot(s):
    """절마다, bullet 항목마다 내용 해시. 상태를 넘길 때 남겨 두고 뒤 상태에서 바뀌었는지 본다"""
    def h(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    out = {}
    for name, lines in s.items():
        out[name] = h("\n".join(l.rstrip() for l in body(lines)))
        for k, v in bullets(lines).items():
            out[f"{name}: {k}"] = h(v)
    return out


def plan_text(p):
    return (ROOT / p["plan"]).read_text(encoding="utf-8")


PATH_TOKEN = re.compile(r"[\w.][\w./-]*")


def plan_files(s):
    """계획이 고치겠다고 적은 코드 파일(files_section 절의 경로 중 code 패턴에 맞는 것).
    표가 있으면 첫 칸만 본다. 설명 칸에 적은 경로를 고칠 파일로 치지 않기 위해서다"""
    lines = s.get(P["files_section"], [])
    rows = [r for t in tables(lines) for r in t if r]
    text = "\n".join(r[0] for r in rows) if rows else "\n".join(lines)
    return sorted({t.strip("`") for t in PATH_TOKEN.findall(text.replace("`", " ")) if is_code(t.strip("`"))})


# 코드 속 숫자

def numbers_in(line, path):
    """한 줄의 숫자 토큰(주석과 문자열 제외). .py는 tokenize, 나머지는 문자열과 주석을 지운 뒤 정규식"""
    if path.endswith(".py"):
        out = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(line + "\n").readline):
                if tok.type == tokenize.NUMBER:
                    out.append(tok.string)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        return out
    line = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`", "", line)
    line = re.split(r"//|#|--", line)[0]
    return re.findall(r"(?<![\w.])(?:\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?|\.\d+)(?![\w.])", line)


def num_value(s):
    try:
        return float(s.replace("_", ""))
    except ValueError:
        try:
            return float(int(s.replace("_", ""), 0))
        except ValueError:
            return None


def new_numbers(commit, files):
    """commit 뒤 커밋에서 files에 새로 들어간 숫자(0과 1 제외)"""
    found = {}
    if not files:
        return found
    path = None
    for line in git("diff", "-U0", f"{commit}..HEAD", "--", *files).splitlines():
        if line.startswith("+++ "):
            path = line[6:]
            continue
        if path is None or not line.startswith("+"):
            continue
        for n in numbers_in(line[1:], path):
            if num_value(n) not in (0.0, 1.0):
                found.setdefault(n, f"{path}: {line[1:].strip()[:80]}")
    return found


# 결과 파일

def result_status(paths, since):
    """결과 파일마다 없거나 since(epoch)보다 오래됐으면 이유. [remote]가 있으면 로컬에 없는 경로를 ssh로 본다"""
    out, remote = [], []
    for x in paths:
        if (ROOT / x).exists():
            if (ROOT / x).stat().st_mtime < since:
                out.append(f"결과 파일이 상태 진입 시각보다 오래됐다: {x}")
        elif CFG["remote"].get("host"):
            remote.append(x)
        else:
            out.append(f"결과 파일이 없다: {x}")
    if remote:
        r = CFG["remote"]
        script = (f"cd {r.get('dir', '~')} && for p in {' '.join(map(shlex.quote, remote))}; do "
                  f't=$(stat -c %Y "$p" 2>/dev/null) || {{ echo MISSING "$p"; continue; }}; '
                  f'[ $t -ge {int(since)} ] || echo OLD "$p"; done')
        port = ["-p", str(r["port"])] if r.get("port") else []
        try:
            res = subprocess.run(["ssh", *port, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", r["host"], script],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            if res.returncode != 0:
                return out + [f"(서버 확인 실패: {res.stderr.strip()[:100]})"]
            for line in res.stdout.splitlines():
                kind, _, x = line.partition(" ")
                out.append(f"결과 파일이 없다: {x}" if kind == "MISSING" else f"결과 파일이 상태 진입 시각보다 오래됐다: {x}")
        except Exception as e:
            out.append(f"(서버 확인 실패: {e!r})")
    return out


# 서버 실행 감지

CMD_AT = r"(?:^|[;&|(]\s*|\b(?:timeout\s+\S+|nohup|setsid)\s+)"
# 작업을 띄우는 경우만 센다. ssh 자체의 -f(원격 명령 따옴표 앞), nohup, setsid, tmux new/send-keys, screen -d.
# tmux ls, tail -f 같은 확인 명령은 세지 않는다
LAUNCH = re.compile(CMD_AT + r"ssh\b(?:[^\n\"']*?\s-[a-zA-Z]*f[a-zA-Z]*\b|[^\n]*?(?:\bnohup\b|\bsetsid\b"
                    r"|\btmux\s+(?:-\S+\s+)*(?:new|new-session|send-keys|send)\b|\bscreen\s+-\S*d))", re.M)
UPLOAD = re.compile(CMD_AT + r"(scp|rsync)\b", re.M)


def remote_on():
    return bool(CFG["remote"].get("host"))


class Lock:
    """상태 파일을 쓰는 명령이 겹치지 않게 한다"""
    STALE = 20 * 60  # 검토 호출 시간 제한(600초)보다 길게

    def __enter__(self):
        self.path = git_path("gate.lock")
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.STALE:
            self.path.unlink(missing_ok=True)
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            raise SystemExit("다른 gate 명령이 진행 중이다(git 폴더의 gate.lock). 끝난 뒤 다시 실행한다")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
