"""Claude Code hook. 플러그인의 hooks/run.py가 이벤트 이름을 넘겨 부른다.

guard(PreToolUse): 상태에 맞지 않는 행동을 막는다. 내부 예외가 나면 막되, 하네스 편집과 읽기, git 복구는 허용한다.
stop(Stop): 이 세션이 고친 코드, 하네스, 상태 파일의 미커밋 변경과, 이번 턴에 다룬 계획이 멈추면 안 되는 상태에 있을 때
  막는다. 서버 작업을 띄운 턴은 답에 남은 시간과 끝나는 시각을 요구하고, PreToolUse hook이 막은 명령은 띄운 것으로 세지 않는다. 이번 턴에 고친 문서의 미커밋 변경은 알리기만 한다. 일반 질문이나 부가 작업 턴, 다른 세션의 작업으로는
  막지 않는다. 5번 연속 막으면 통과시킨다.
session(SessionStart): 진행 중인 계획과 할 일 전체, status_file 절의 항목 머리를 6,000자 안으로 넣는다.
  prompt(UserPromptSubmit): 계획마다 한 줄만 넣는다.
  주입 hook은 예외가 나도 통과한다.
"""
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

STOP_CAP = 5  # 근거 없이 정한 값. Claude Code는 도구 호출 없이 8번 막히면 강제로 끝낸다
# SessionStart 주입 상한. hook 출력이 10,000자를 넘으면 Claude Code가 파일로 빼고 앞 2KB만 넣는다. 여유를 둔 값이다
CONTEXT_CAP = 6000
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
ARG = r"(?:\"[^\"\n]*\"|'[^'\n]*'|\S+)"
CLI_SEG = re.compile(r"^\s*(python3?|py)(\s+-X\s+utf8)?\s+\.gate/gate\.py\b")
# 인자를 프로그램으로 읽지 않는 명령만 둔다. awk, sed, find, xargs, sort -o는 파일을 쓰거나 명령을 돌릴 수 있어 뺀다
READ_SEG = re.compile(r"^\s*(cd|cat|type|head|tail|ls|dir|wc|grep|rg|findstr|cut|echo|du|stat|nl|Get-Content"
                      r"|Get-ChildItem|Select-String|Measure-Object|Select-Object"
                      r"|git(?:\s+-C\s+" + ARG + r")?\s+(add|commit|diff|log|show|status|checkout|restore|blame|grep"
                      r"|ls-files|rev-parse|branch))\b")
DROP_REDIR = re.compile(r"\s(?:2>\s*/dev/null|2>&1|2>\s*\$null)(?=[\s;&|]|$)")  # 오류 출력을 버리는 리다이렉트만 허용
STATE_TOUCH = re.compile(r"\.gate/state\.json|\.gate/gate\.py|import\s+gatekit|from\s+gatekit|gate\.lock")
HOOK_OFF = re.compile(r"--no-verify\b|\bgit\b[^;&|\n]*\bcommit\b[^;&|\n]*\s-[a-zA-Z]*n[a-zA-Z]*\b"
                      r"|core\.hooksPath(?!\s+\.githooks(\s|$))|disableAllHooks")
# 커밋 메시지 인자. 메시지 속 단어를 -n으로 잘못 읽지 않게 검사 전에 뺀다
MSG_ARG = re.compile(r"(\s(?:-[a-zA-Z]*m|--message)(?:\s+|=))(?:\"[^\"]*\"|'[^']*')")
INCIDENT = re.compile(r"\bincident\b[^;&|\n]*?--quote\s+(?:\"([^\"]*)\"|'([^']*)')")
ADOPT = re.compile(r"\badopt\s+(\S+)\s+--approved\s+(?:\"([^\"]*)\"|'([^']*)')")
SHELL_WRITE = re.compile(r"(>>?|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|Set-Content|Out-File|Add-Content|Copy-Item|Move-Item)"
                         r"[^\n|;&]*?([\w.][\w./-]*\.\w+)")


def split_cmd(cmd):
    """따옴표 밖의 &&, ||, ;, |로 나눈 조각. 따옴표 밖 리다이렉트(오류 출력 버리기 제외), 명령 치환, 배경 실행,
    줄바꿈이나 큰따옴표 안 명령 치환이 있으면 None"""
    cmd = DROP_REDIR.sub(" ", cmd)
    segs, cur, q, i = [], "", None, 0
    while i < len(cmd):
        c, two = cmd[i], cmd[i:i + 2]
        if q:
            if c == q:
                q = None
            elif q == '"' and (c == "`" or two == "$("):
                return None
            cur, i = cur + c, i + 1
        elif c in "\"'":
            q, cur, i = c, cur + c, i + 1
        elif two in ("&&", "||"):
            segs, cur, i = segs + [cur], "", i + 2
        elif c in ";|":
            segs, cur, i = segs + [cur], "", i + 1
        elif c in "<>`&\n" or two == "$(":
            return None
        else:
            cur, i = cur + c, i + 1
    return None if q else [s for s in segs + [cur] if s.strip()]


def is_read(cmd):
    """읽기 명령만 이은 명령인가"""
    segs = split_cmd(cmd)
    return bool(segs) and all(READ_SEG.match(s) for s in segs)


def state_safe(cmd, state_rel=".gate/state.json"):
    """읽기 명령과 gate 명령만 이은 명령인가. 상태 파일을 쓰는 길은 gate 명령뿐이다. 본문에 gate나 상태 파일 이름이 한 번도
    나오지 않는 Python heredoc은 상태를 다루지 않는 단계로 보고 이을 수 있다(문서를 고치는 heredoc과 gate 명령을 이었다가 막힌
    일이 대회 하네스의 마지막 harness: 커밋 뒤 막힘 15번 중 9번이었다)"""
    keep = re.compile(r"gate|" + re.escape(state_rel), re.I)
    segs = split_cmd(HEREDOC.sub(lambda m: m.group(0) if keep.search(m.group(4)) else f"{NO_STATE} ; ", cmd))
    return bool(segs) and all(READ_SEG.match(s) or CLI_SEG.match(s) or s.strip() == NO_STATE for s in segs)


HEREDOC = re.compile(r"\b(python3?|py)(\s+-X\s+utf8)?\s+-\s+<<\s*'?(\w+)'?\n(.*?)\n\3(?:\n|$)", re.S)
NO_STATE = "__gate_heredoc__"  # 상태를 다루지 않는 heredoc 자리. 실제 명령이 이 이름일 수 없다


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


CMD = ""  # 막은 명령(점검 자료)
KEY = ""  # 막은 행동의 열쇠. 같은 행동이 나중에 통과하면 판정을 붙인다


def pending(update=None):
    """막힌 뒤 아직 통과하지 않은 행동의 열쇠 목록(git 폴더). update를 주면 그 목록으로 바꾼다"""
    from .core import git_path
    f = git_path("gate-pending.json")
    if update is None:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    f.write_text(json.dumps(update[-200:], ensure_ascii=False), encoding="utf-8")


def deny(reason, part=""):
    try:  # 하네스 점검 자료. 오탐을 가려낼 수 있게 명령 앞부분도 남긴다
        from .core import log_event
        log_event("guard", reason, **({"cmd": CMD[:200]} if CMD else {}), **({"key": KEY} if KEY else {}),
                  **({"part": part} if part else {}))
        if KEY and KEY not in pending():
            pending(pending() + [KEY])
    except Exception:
        pass
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})
    sys.exit(0)


def rel(path, root):
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (TypeError, ValueError, OSError):
        return None


# guard

def code_gate(path, flow):
    """코드 파일 path를 고칠 수 없는 이유나 None. 코드 상태인 계획의 files_section에 적은 파일만 열린다"""
    from .core import P, code_open, open_files, tests_only
    if not code_open(flow):
        return (f"코드를 고치는 상태인 계획이 없어 {path} 수정을 막았다. python -X utf8 .gate/gate.py status로 상태를 보고, "
                "계획을 시작하거나 next로 코드 상태까지 진행한다.")
    if tests_only(flow, path):
        return tests_only(flow, path)
    opened = open_files(flow)
    if path not in opened:
        return (f"{path}는 코드를 고치는 상태인 계획의 '{P['files_section']}' 절에 없어 수정을 막았다. 고칠 계획의 표에 먼저 "
                f"적는다(지금 열린 파일: {', '.join(sorted(opened)) or '없음'}).")
    return None


def guard_passed():
    """앞에서 막혔던 행동이 통과했으면 남긴다(score.verdicts가 구조와 엔진이 그대로였는지 본다)"""
    try:
        keys = pending()
        if KEY and KEY in keys:
            from .core import log_event
            log_event("guard_pass", "", key=KEY)
            pending([k for k in keys if k != KEY])
    except Exception:
        pass


def guard(inp):
    global CMD, KEY
    from .core import (ROOT, STATE, dirty_code, exec_open, is_code_any, is_protected, load, remote_exec_paths, remote_on,
                       run_open, LAUNCH, UPLOAD)
    tool, ti = inp.get("tool_name", ""), inp.get("tool_input", {})
    state_rel = STATE.relative_to(ROOT).as_posix()
    if tool in EDIT_TOOLS:
        path = rel(ti.get("file_path") or ti.get("notebook_path"), ROOT) or ""
        KEY = f"edit:{path}"
        if path == state_rel or path.startswith(".git/"):
            deny(f"{path}는 직접 쓰지 않는다. 상태는 python -X utf8 .gate/gate.py 명령으로만 바꾼다.")
        if path.startswith(".githooks/"):
            deny(f"{path}는 설치 스크립트가 쓰는 git hook이라 직접 고치지 않는다. 커밋 전에 돌릴 명령은 제안서로 "
                 "[project] pre_commit에 넣고, hook 자체를 바꿔야 하면 사용자에게 알린다.")
        if is_protected(path):
            deny(f"{path}는 하네스 구조 파일이라 직접 고치지 않는다. .gate/proposals/H###.md에 제안서를 쓰고 "
                 "check-proposal을 통과한 뒤 adopt로 바꾼다(evolve skill).")
        flow = load()
        if is_code_any(path, flow):  # 지금 구조나 진행 중인 계획의 구조 버전 중 하나라도 코드로 보면 코드다
            why = code_gate(path, flow)
            if why:
                deny(why)
        return
    if tool not in ("Bash", "PowerShell"):
        return
    cmd = CMD = ti.get("command", "").replace("\\", "/")
    KEY = f"cmd:{cmd[:200]}"
    from .core import CFG
    for g in CFG["guard"]:  # 부품이 정한 막을 명령
        if re.search(g["pattern"], cmd):
            deny(g["reason"], g.get("part", ""))
    if HOOK_OFF.search(MSG_ARG.sub(r"\1", cmd)):
        deny("git hook이나 Claude Code hook을 끄거나 우회하는 명령은 막는다.")
    if (STATE_TOUCH.search(cmd) or state_rel in cmd) and not state_safe(cmd, state_rel):
        deny("상태는 python -X utf8 .gate/gate.py 명령으로만 바꾼다. 읽기 명령(cat, grep, git log 등)과 이을 수는 있지만 "
             "Python 스크립트, 리다이렉트, 다른 쓰기 명령으로 다루지 않는다.")
    # 승인 방식이 user면 구조 변경은 사용자가 이번 턴에 승인했을 때만(이은 명령 안도 본다). auto는 check-proposal 통과로 충분하다
    if CFG["project"].get("approval") == "user" and re.search(r"\.gate/gate\.py\s+adopt\b", cmd):
        m = ADOPT.search(cmd)
        quote = " ".join((m.group(2) or m.group(3) or "").split()) if m else ""
        prompt = read_transcript(inp["transcript_path"], LAUNCH)["prompt"] if inp.get("transcript_path") else ""
        if not quote or quote not in prompt:
            deny("adopt --approved의 문장이 이번 턴 사용자 입력에 없다. 제안서의 바꾸는 것과 근거를 사용자에게 보이고, "
                 "사용자가 승인한 문장을 그대로 넣는다.")
    m = INCIDENT.search(cmd) if re.search(r"\.gate/gate\.py\s+incident\b", cmd) else None
    if m:  # 사용자가 지적한 사고는 그 문장이 이번 턴 입력에 있어야 한다
        quote = " ".join((m.group(1) or m.group(2) or "").split())
        prompt = read_transcript(inp["transcript_path"], LAUNCH)["prompt"] if inp.get("transcript_path") else ""
        if not quote or quote not in prompt:
            deny("incident --quote의 문장이 이번 턴 사용자 입력에 없다. 사용자가 쓴 문장을 그대로 넣고, 결과를 보고 "
                 "스스로 찾은 결함이면 --quote를 뺀다.")
    flow = load()
    for m in SHELL_WRITE.finditer(cmd):
        if is_protected(m.group(2)):
            deny(f"{m.group(2)}는 하네스 구조 파일이라 셸로 쓰지 않는다. 제안서와 adopt로 바꾼다.")
        if is_code_any(m.group(2), flow):
            why = code_gate(m.group(2), flow)
            if why:
                deny(f"셸로 {m.group(2)}를 쓰는 것을 막았다. " + why)
    execs = remote_exec_paths(cmd, flow) if remote_on() else []
    if remote_on() and (LAUNCH.search(cmd) or UPLOAD.search(cmd) or execs):
        d = dirty_code(flow=flow)
        if d:
            deny("커밋하지 않은 코드가 있어 서버 실행과 업로드를 막았다. 커밋한 뒤 다시 실행한다: "
                 + ", ".join(p for _, p in d[:8]))
        if LAUNCH.search(cmd) and not run_open(flow):
            deny("실행 상태이고 gate run 기록(예상 종료 시각과 근거)이 있는 계획이 없어 서버 실행을 막았다. "
                 "python -X utf8 .gate/gate.py run P### --eta HH:MM --basis \"...\"를 먼저 한다.")
        if execs and not exec_open(flow):
            deny(f"ssh로 저장소 코드({', '.join(execs)})를 돌리려면 gate run 기록이 있는 실행 상태 계획이나 [remote] "
                 "exec_states 상태의 계획이 있어야 한다. nvidia-smi, tail, ps 같은 서버 확인 명령은 막지 않는다.")


def recoverable(inp, root):
    """하네스가 깨졌을 때도 허용하는 행동: 하네스 파일 편집, 읽기와 git 복구"""
    tool, ti = inp.get("tool_name", ""), inp.get("tool_input", {})
    if tool in EDIT_TOOLS:
        path = rel(ti.get("file_path") or ti.get("notebook_path"), root) or ""
        return path.startswith((".gate/", ".githooks/"))
    if tool in ("Bash", "PowerShell"):
        return is_read(ti.get("command", "").replace("\\", "/"))
    return True


# stop

def result_text(x):
    """tool_result의 본문. 문자열이거나 text 조각 목록이다"""
    body = x.get("content")
    return body if isinstance(body, str) else " ".join(y.get("text", "") for y in body or [] if isinstance(y, dict))


def read_transcript(path, launch, bg_exec=None):
    """bg_exec(cmd)가 참인 run_in_background 명령도 서버 실행으로 센다(그 자리에서 돌리는 ssh)"""
    from .core import ROOT
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") in ("user", "assistant") and not d.get("isSidechain"):
                rows.append(d)

    def text_of(d):
        c = d["message"]["content"]
        return c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c if x.get("type") == "text")

    def is_prompt(d):
        c = d["message"]["content"]
        return d["type"] == "user" and (isinstance(c, str) or not any(x.get("type") == "tool_result" for x in c)) \
            and not text_of(d).startswith("Stop hook feedback")

    prompts = [i for i, d in enumerate(rows) if is_prompt(d)]
    start = prompts[-1] if prompts else 0
    own, edited, cli_calls, own_turn, edited_turn = set(), set(), set(), set(), set()
    for i, d in enumerate(rows):
        c = d["message"]["content"]
        if isinstance(c, str):
            continue
        for x in c:
            ti = x.get("input", {}) if x.get("type") == "tool_use" else {}
            found, files = set(), set()
            if x.get("type") == "tool_use" and x.get("name") in EDIT_TOOLS:
                r = rel(ti.get("file_path") or ti.get("notebook_path"), ROOT)
                if r:
                    files.add(r)
            elif x.get("type") == "tool_use" and ".gate/gate.py" in str(ti.get("command", "")).replace("\\", "/"):
                found = set(re.findall(r"\b[A-Z]+\d{3}\b", ti["command"]))
                cli_calls.add(x.get("id"))
            elif x.get("type") == "tool_result" and x.get("tool_use_id") in cli_calls:
                found = set(re.findall(r"^([A-Z]+\d{3}) ", result_text(x), re.M))  # start가 출력한 새 번호
            own |= found
            edited |= files
            if i >= start:
                own_turn |= found
                edited_turn |= files
    launched, text = False, []
    # PreToolUse hook이 실행 전에 막은 명령은 띄운 것으로 세지 않는다
    blocked = {x.get("tool_use_id") for d in rows[start:] if d["type"] == "user" and not isinstance(d["message"]["content"], str)
               for x in d["message"]["content"]
               if x.get("type") == "tool_result" and x.get("is_error") and "hook error" in result_text(x)}
    for d in rows[start:]:
        c = d["message"]["content"]
        if d["type"] != "assistant" or isinstance(c, str):
            continue
        for x in c:
            if x.get("type") == "tool_use" and x.get("name") in ("Bash", "PowerShell") and x.get("id") not in blocked:
                cmd = x["input"].get("command", "").replace("\\", "/")
                if launch.search(cmd) or (bg_exec and x["input"].get("run_in_background") and bg_exec(cmd)):
                    launched = True
            elif x.get("type") == "text":
                text.append(x["text"])
    prompt = " ".join(text_of(rows[start]).split()) if rows else ""
    notified = "<task-notification>" in prompt
    return {"own": own, "edited": edited, "own_turn": own_turn, "edited_turn": edited_turn, "launched": launched,
            "text": "\n".join(text), "prompt": prompt, "notified": notified}


def eta_passed(run):
    start = datetime.fromisoformat(run["at"])
    h, m = map(int, run["eta"].split(":"))
    eta = start.replace(hour=h, minute=m)
    if eta < start:
        eta += timedelta(days=1)
    return datetime.now() >= eta


def stop_problems(inp):
    from .core import (P, ROOT, STATE, cfg_for, dirty, is_code_any, is_harness, load, remote_exec_paths, remote_on,
                       sections, state_def, LAUNCH)
    from .checks import results
    flow = load()
    t = read_transcript(inp["transcript_path"], LAUNCH, lambda c: remote_exec_paths(c, flow))
    state_rel = STATE.relative_to(ROOT).as_posix()
    edited, edited_turn = set(t["edited"]), set(t["edited_turn"])
    if t["own_turn"]:  # 이번 턴의 gate 명령이 쓴 상태 파일
        edited.add(state_rel)
        edited_turn.add(state_rel)

    def blocking(path):  # 코드, 하네스, 상태 파일은 막고 문서는 알리기만 한다
        return path == state_rel or is_code_any(path, flow) or is_harness(path)

    problems, notes = [], []
    lines = dirty(*(P["tracked"] or ["."]))  # 저장소 전체에 하나인 목록이라 지금 구조를 따른다
    code = [f"{st} {path}" for st, path in lines if path in edited and blocking(path)]
    docs = [f"{st} {path}" for st, path in lines if path in edited_turn and not blocking(path)]
    if code:
        problems.append("이 세션이 고친 코드나 하네스 파일에 커밋하지 않은 변경이 있다. 단위가 끝났으면 커밋하고, 진행 중이면 "
                        "그 상태를 계획 파일에 적는다.\n" + "\n".join(code))
    if docs:
        notes.append("이번 턴에 고친 문서가 아직 커밋되지 않았다. 검토가 끝나면 커밋한다.\n" + "\n".join(docs))
    if remote_on() and t["launched"] and not (re.search(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", t["text"])
                                               and re.search(r"\d+\s*(분|시간)", t["text"])):
        problems.append("이번 턴에 서버 작업을 띄웠는데 답에 남은 시간과 끝나는 시각(예: 약 20분, 01:05쯤)이 없다.")
    # 이번 턴에 gate 명령으로 다룬 계획, 결과 알림으로 시작한 턴이면 이 세션이 다룬 계획.
    # 일반 질문이나 부가 작업 턴은 계획 상태로 막지 않는다
    watch = t["own_turn"] | (t["own"] if t["notified"] else set())
    for pid in sorted(pid for pid in watch if pid in flow["plans"]):
        p = flow["plans"][pid]
        if p.get("hold") and p["hold"]["quote"] in t["prompt"]:
            continue
        st = state_def(p)
        if p["state"] in cfg_for(p)["project"]["stop_states"]:
            problems.append(f"{pid} {p['title']}가 {p['state']}에 머물러 있다. 할 일을 하고 "
                            f"python -X utf8 .gate/gate.py next {pid}로 넘긴다.")
        elif st.get("run") and p.get("run") and (t["notified"] or eta_passed(p["run"])):
            spec = next((c for c in st.get("checks", []) if c["check"] == "results"), None)
            if spec:
                s = sections((ROOT / p["plan"]).read_text(encoding="utf-8"))
                ctx = {"pid": pid, "p": p, "s": s, "state": p["state"], "cfg": cfg_for(p)}
                if not results(ctx, spec):
                    problems.append(f"{pid} {p['title']}의 결과 파일이 모두 생겼다. python -X utf8 .gate/gate.py next {pid}로 "
                                    "넘어가 기록한다.")
    return problems, notes


def stop(inp):
    try:
        problems, notes = stop_problems(inp)
    except Exception as e:  # 막는 hook이라 내부 오류도 막는다
        problems, notes = [f"stop hook 내부 오류: {e!r}. .gate/gatekit/hooks.py를 고친다."], []
    counter = Path(tempfile.gettempdir()) / f"gatekit_stop_{inp.get('session_id', 'x')}.json"
    n = json.loads(counter.read_text())["n"] if counter.exists() else 0
    if not problems:
        counter.unlink(missing_ok=True)
        if notes:
            emit({"systemMessage": "\n".join(notes)})
        return
    if n + 1 >= STOP_CAP:
        counter.unlink(missing_ok=True)
        emit({"systemMessage": f"Stop hook이 {STOP_CAP}번 연속 막아 이번에는 통과시켰다. 남은 문제:\n" + "\n".join(problems)})
        return
    counter.write_text(json.dumps({"n": n + 1}))
    try:  # 하네스 점검 자료
        from .core import log_event
        for x in problems:
            log_event("stop", x.splitlines()[0])
    except Exception:
        pass
    emit({"decision": "block", "reason": "\n\n".join(problems)})


# 주입

def context(event):
    from .cli import status_text
    from .core import CLOSED, ENGINE, P, ROOT, load
    flow = load()
    if event == "prompt":  # 매 메시지에는 계획마다 한 줄만
        active = [f"{pid} {p['title']}: {p['state']}" for pid, p in flow["plans"].items() if p["state"] not in CLOSED]
        if not active:
            return
        emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
              "[gate] 진행 중인 계획: " + "; ".join(active) + ". 계획 작업이면 python -X utf8 .gate/gate.py status로 할 일을 "
              "보고 그 상태에 맞춰 하며, 다음 상태로는 next로만 넘어간다. 흐름과 관계없는 질문이나 부가 작업이면 이 목록을 "
              "따르지 않는다."}})
        return
    parts = ["[gate] 진행 중인 계획과 지금 상태의 할 일이다(.gate/GATE.md). 계획 작업은 이 상태에 맞춰 하고, 다음 상태로는 "
             "python -X utf8 .gate/gate.py next로만 넘어간다. 흐름과 관계없는 질문이나 부가 작업이면 따르지 않는다.",
             status_text(flow)]
    if P["status_file"] and (ROOT / P["status_file"]).exists():
        text = (ROOT / P["status_file"]).read_text(encoding="utf-8")
        from .records import fields
        if fields(text):
            parts.append(f"{P['status_file']} 머리: " + ", ".join(f"{k} {v}" for k, v in fields(text).items()))
        parts.append(f"아래 {P['status_file']} 절은 항목 머리만 줄인 것이다. 계획 작업이면 전문을 먼저 읽는다.")
        for title in P["status_sections"]:
            m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
            parts.append(f"## {title}(머리)\n" + "\n".join(heads(m.group(1).strip() if m else "")))
    moved = [pid for pid, p in flow["plans"].items() if p["state"] not in CLOSED and p.get("engine", ENGINE) != ENGINE]
    if moved:
        parts.append(f"[엔진 변경] {', '.join(moved)}는 시작한 뒤 엔진(.gate/gatekit)이 바뀌었다. 구조 버전은 그대로지만 "
                     "검사 코드가 달라졌을 수 있으니, 다음 next가 예상과 다르게 막거나 통과하면 사용자에게 알린다.")
    from .evolve import review_due
    why = review_due(flow)
    if why:
        parts.append("[하네스 점검 권장] " + "; ".join(why) + ". 사용자에게 점검할지 한 번 묻고, 하기로 하면 evolve skill로 "
                     "진단하고 구조 변경 제안서를 쓴다. 지금 요청이 급하면 그 일을 먼저 한다.")
    from .records import due
    tidy = due(flow)
    if tidy:
        parts.append("[기록 정리 권장] " + "; ".join(tidy) + ". 사용자에게 정리할지 한 번 묻고, 하기로 하면 정리 종류의 "
                     "계획을 시작한다(python -X utf8 .gate/gate.py records로 크기를 본다).")
    ctx = "\n\n".join(parts)
    if len(ctx) > CONTEXT_CAP:
        ctx = ctx[:CONTEXT_CAP] + f"\n…(잘림. 전문은 {P['status_file'] or '상태 파일'}과 python -X utf8 .gate/gate.py status)"
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}})


def cut(s, n=160):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def heads(body_):
    """번호 항목, bullet, 표 행의 머리. 없으면 첫 문단의 머리"""
    items = re.findall(r"^((?:\d+\.|[-*]) .*)", body_, re.M)
    rows = [l for l in body_.splitlines() if l.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", l)][1:]
    if items:
        return [cut(x) for x in items]
    if rows:
        return ["- " + cut(r.strip("| ").split("|")[0]) for r in rows]
    return [cut(body_.split("\n\n")[0], 400)] if body_ else ["(없음)"]


def main(event, root):
    inp = json.load(sys.stdin)
    if event == "guard":
        try:
            guard(inp)
            guard_passed()
        except SystemExit:
            raise
        except Exception as e:
            if not recoverable(inp, root):
                deny(f"guard hook 내부 오류로 막았다: {e!r}. .gate/gatekit/나 .gate/config.toml을 고친다"
                     "(하네스 편집과 읽기, git checkout, restore는 허용된다).")
    elif event == "stop":
        stop(inp)
    else:
        try:
            context(event)
        except Exception as e:  # 주입 hook은 막지 않는다
            emit({"systemMessage": f"gate 주입 hook 오류: {e!r}"})
