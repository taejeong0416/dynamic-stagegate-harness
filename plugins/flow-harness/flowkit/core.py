"""설정, 상태 파일, 계획 파일 파싱, git 도우미.

이 패키지는 프로젝트의 .flow/flowkit/에 복사되어 돈다. 프로젝트 루트는 .flow/의 부모다.
"""
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import time
import tokenize
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW_DIR = ROOT / ".flow"
CLOSED = ("DONE", "ABANDONED")

DEFAULTS = {
    "plans_dir": "plans",
    "state_file": ".flow/state.json",
    "plan_prefix": "P",
    "code": [],
    "tracked": [],
    "harness": [".flow/flowkit/", ".flow/flow.py", ".flow/config.toml", ".flow/templates/", ".flow/FLOW.md",
                ".githooks/"],
    "harness_prefix": "harness:",
    "max_visits": 3,
    "files_section": "변경 파일",
    "unreviewed": [],
    "stop_states": [],
    "decisions_file": "",
    "status_file": "",
    "status_sections": [],
    "pre_commit": [],
}


def load_config():
    raw = tomllib.loads((FLOW_DIR / "config.toml").read_text(encoding="utf-8"))
    cfg = {"project": DEFAULTS | raw.get("project", {}), "kinds": raw.get("kinds", {}),
           "states": raw.get("states", {}), "stages": raw.get("stages", []), "remote": raw.get("remote", {}),
           "review": raw.get("review", {})}
    for k, v in cfg["kinds"].items():
        missing = [s for s in v["states"] if s not in cfg["states"]]
        if missing:
            raise SystemExit(f"config.toml: {k} 종류의 상태 {', '.join(missing)}가 [states]에 없다")
    return cfg


CFG = load_config()
P = CFG["project"]
STATE = ROOT / P["state_file"]
PLANS = ROOT / P["plans_dir"]


def match(path, globs):
    """fnmatch의 *는 /도 넘는다. /로 끝나는 패턴은 그 폴더 아래 전부"""
    return any(path.startswith(g) if g.endswith("/") else fnmatch.fnmatch(path, g) for g in globs)


def is_harness(path):
    return match(path, P["harness"])


def is_code(path):
    return match(path, P["code"]) and not is_harness(path)


# git

def git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8").stdout


def head():
    return git("rev-parse", "--short", "HEAD").strip()


def changed_since(commit, *paths):
    out = git("diff", "--name-only", f"{commit}..HEAD", "--", *paths)
    return set(out.split())


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


def code_open(flow):
    return any(CFG["states"].get(p["state"], {}).get("code") for p in flow["plans"].values()
               if p["state"] not in CLOSED)


def run_open(flow):
    return any(CFG["states"].get(p["state"], {}).get("run") and p.get("run") for p in flow["plans"].values()
               if p["state"] not in CLOSED)


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


def template_path(kind):
    return FLOW_DIR / CFG["kinds"][kind]["template"]


def structure_problems(p, text):
    """절 제목이 틀과 같은 순서로 한 번씩 있고, 틀의 bullet 항목이 모두 있는지"""
    tpl = template_path(p["kind"]).read_text(encoding="utf-8")
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


def plan_hash(text):
    """검토한 내용의 해시. 검토 뒤에 채우는 절(unreviewed)을 뺀다"""
    parts = [text.split("\n## ")[0]]
    for name, lines in sections(text).items():
        if name not in P["unreviewed"]:
            parts.append(name + "\n" + "\n".join(lines))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def plan_text(p):
    return (ROOT / p["plan"]).read_text(encoding="utf-8")


PATH_TOKEN = re.compile(r"[\w.][\w./-]*")


def plan_files(s):
    """계획이 고치겠다고 적은 코드 파일(files_section 절의 경로 중 code 패턴에 맞는 것)"""
    text = "\n".join(s.get(P["files_section"], []))
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
        script = (f"cd {r.get('dir', '~')} && for p in {' '.join(remote)}; do "
                  f"t=$(stat -c %Y $p 2>/dev/null) || {{ echo MISSING $p; continue; }}; "
                  f"[ $t -ge {int(since)} ] || echo OLD $p; done")
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
LAUNCH = re.compile(CMD_AT + r"ssh\b[^\n]*(\s-[a-zA-Z]*f[a-zA-Z]*\b|\bnohup\b|\btmux\b|\bscreen\b|\bsetsid\b)", re.M)
UPLOAD = re.compile(CMD_AT + r"(scp|rsync)\b", re.M)


def remote_on():
    return bool(CFG["remote"].get("host"))


class Lock:
    """상태 파일을 쓰는 명령이 겹치지 않게 한다"""
    STALE = 20 * 60  # 검토 호출 시간 제한(600초)보다 길게

    def __enter__(self):
        self.path = git_path("flow.lock")
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.STALE:
            self.path.unlink(missing_ok=True)
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            raise SystemExit("다른 flow 명령이 진행 중이다(git 폴더의 flow.lock). 끝난 뒤 다시 실행한다")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
