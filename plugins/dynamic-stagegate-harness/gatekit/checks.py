"""넘어가는 조건 검사 함수. config.toml의 [states.X] checks 목록에서 이름으로 부른다.

검사 함수는 (ctx, spec)을 받아 빠진 항목 문장 목록을 돌려준다. 빈 목록이면 통과다.
ctx: pid, p(계획 기록), text(계획 파일), s(절), flow(상태 파일), state(지금 상태), cfg(계획이 쓰는 구조 버전)
spec에 only = [종류들] 이나 skip = [종류들] 을 두면 그 종류에만 걸거나 그 종류는 뺀다.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess

from .core import (CFG, ROOT, bullets, body, changed_since, dirty_code, filled, first, git, git_path, is_code, is_test,
                   new_numbers, now, num_value, plan_files, plan_hash, result_status, snapshot, tables)

CHECKS = {}


def check(fn):
    CHECKS[fn.__name__.removesuffix("_section")] = fn
    return fn


def sec(ctx, spec, default=None):
    return ctx["s"].get(spec.get("section", default), [])


@check
def filled_section(ctx, spec):
    """절이 비어 있지 않다. items를 주면 그 bullet 항목마다 값이 있다"""
    lines = sec(ctx, spec)
    if spec.get("items"):
        b = bullets(lines)
        return [f"'{spec['section']}' 절의 '{k}' 항목이 비었다" for k in spec["items"] if not b.get(k)]
    return [] if body(lines) else [f"'{spec['section']}' 절이 비었다"]


@check
def table(ctx, spec):
    """절의 index번째 표에 칸이 모두 찬 행이 min_rows개 이상 있다"""
    t = tables(sec(ctx, spec))
    i, n = spec.get("index", 0), spec.get("min_rows", 1)
    rows = filled(t[i]) if len(t) > i else []
    return [] if len(rows) >= n else [f"'{spec['section']}' 절 표에 칸이 모두 찬 행이 {n}개 이상 있어야 한다(지금 {len(rows)}개)"]


@check
def covers(ctx, spec):
    """from 절 표의 첫 칸이 모두 section 절 표의 첫 칸에 있다. 수용 기준마다 결과를 적었는지 본다"""
    def firsts(name):
        return [" ".join(r[0].split()) for t in tables(ctx["s"].get(name, [])) for r in filled(t)]
    have = set(firsts(spec["section"]))
    return [f"'{spec['from']}' 표의 '{x}'가 '{spec['section']}' 표 첫 칸에 없다. 기준마다 한 행씩 그대로 옮겨 적는다"
            for x in firsts(spec["from"]) if x not in have]


@check
def files_listed(ctx, spec):
    """계획이 고칠 코드 파일을 적었다"""
    return [] if plan_files(ctx["s"]) else [f"'{CFG['project']['files_section']}' 절에 config의 code 패턴에 맞는 파일이 없다"]


@check
def files_changed(ctx, spec):
    """적은 코드 파일이 이 상태에 처음 들어온 뒤의 커밋에서 모두 바뀌었다. any = true면 하나 이상"""
    since = first(ctx["p"], ctx["state"])["commit"]
    files = plan_files(ctx["s"])
    changed = changed_since(since, *files) if files else set()
    if spec.get("any"):
        return [] if changed else [f"적은 코드 파일 중 {ctx['state']}에 들어온 뒤 커밋에서 바뀐 것이 없다"]
    return [f"{f}가 {ctx['state']}에 들어온 뒤 커밋에서 바뀌지 않았다" for f in files if f not in changed]


@check
def clean(ctx, spec):
    """코드에 커밋하지 않은 변경이 없다"""
    d = dirty_code()
    return [] if not d else ["커밋하지 않은 코드 변경이 있다: " + ", ".join(p for _, p in d[:8])]


@check
def numbers(ctx, spec):
    """이 상태에 처음 들어온 뒤 코드에 새로 들어간 숫자(0과 1 제외)가 근거 표의 첫 칸에 모두 있다"""
    name = spec.get("section", "값 근거")
    vals = {num_value(r[0]) for t in tables(ctx["s"].get(name, [])) for r in t if r and r[0]}
    since = first(ctx["p"], ctx["state"])["commit"]
    return [f"'{name}' 표의 값 칸에 없는 숫자 {n} ({where}). 값 칸에는 숫자 하나만 쓴다"
            for n, where in new_numbers(since, plan_files(ctx["s"])).items() if num_value(n) not in vals]


@check
def sample(ctx, spec):
    """표본 실행 절의 명령, 결과 요약, 커밋이 있고, 커밋이 적은 코드 파일을 마지막으로 바꾼 커밋이다"""
    name = spec.get("section", "표본 실행")
    b = bullets(ctx["s"].get(name, []))
    files = plan_files(ctx["s"])
    last = git("log", "-1", "--format=%h", "--", *files).strip() if files else ""
    c = b.get("커밋", "")
    if not b.get("명령") or not b.get("결과 요약") or not c or c[:7] != last[:7]:
        return [f"'{name}' 절의 명령, 결과 요약, 커밋(코드 파일을 마지막으로 바꾼 커밋 {last})이 맞지 않다"]
    return []


@check
def results(ctx, spec):
    """적은 결과 파일이 모두 있고 이 상태에 들어온 뒤에 만들어졌다(remote가 있으면 서버도 본다)"""
    name, item = spec.get("section", "실행 계획"), spec.get("item", "결과 파일")
    paths = bullets(ctx["s"].get(name, [])).get(item, "").replace(",", " ").split()
    if not paths:
        return [f"'{name}' 절의 '{item}'이 비었다"]
    bad = [x for x in paths if x.startswith((CFG["project"]["plans_dir"].rstrip("/") + "/", ".gate/"))]
    return [f"결과 파일로 계획 문서를 쓸 수 없다: {x}" for x in bad] or \
        result_status(paths, ctx["p"]["entered"][ctx["state"]]["ts"])


# 테스트를 돌리지 못한 종료 코드: pytest의 사용 오류(4)와 모은 테스트 없음(5), 셸의 실행 불가(126)와 명령 없음(127)
NOT_RUN = [4, 5, 126, 127]
SHELL_WORDS = {"cd", "echo", "exit", "set", "export", "test", "true", "false", "call", "type", "source", "."}


def missing_program(cmd):
    """명령의 첫 프로그램을 PATH와 저장소에서 찾지 못했으면 그 이름. cmd.exe는 없는 명령에도 1을 돌려줘서 따로 본다"""
    try:
        words = shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return None
    words = [w.strip('"') for w in words if not re.fullmatch(r"\w+=.*", w)]
    if not words or not re.fullmatch(r"[\w./\\:-]+", words[0]) or words[0] in SHELL_WORDS:
        return None
    return None if shutil.which(words[0]) or (ROOT / words[0]).exists() else words[0]


def test_fingerprint(ctx, cmd):
    """명령과, 계획을 시작한 뒤 바뀐 테스트 파일의 커밋된 내용"""
    files = sorted(f for f in changed_since(ctx["p"]["entered"]["_start"]["commit"]) if is_test(f))
    blobs = git("ls-tree", "-r", "HEAD", "--", *files) if files else ""
    return hashlib.sha256((cmd + "\n" + blobs).encode("utf-8")).hexdigest()[:16]


@check
def command(ctx, spec):
    """명령이 기대한 대로 끝난다. run에 명령을 주거나, use로 [project]의 test나 affected 명령을 쓰거나,
    section과 item으로 계획 파일에 적은 명령을 쓴다. affected가 비었으면 test를, use가 가리키는 명령이 비었으면
    계획 파일의 명령을 쓴다. 명령의 {files}는 계획의 코드 파일로 바뀐다. optional = true면 명령이 없을 때 통과한다.
    expect = "fail"이면 실패해야 통과한다(버그 재현, 테스트 먼저 쓰기). 이때 reject의 종료 코드(기본 NOT_RUN)나
    찾지 못한 프로그램은 테스트를 돌리지 못한 것으로 보고 막는다. 같은 명령과 같은 테스트 파일로 실패를 한 번
    확인했으면 다시 돌리지 않는다. 구현한 뒤 back으로 이 상태에 돌아와도 넘어갈 수 있게 하기 위해서다"""
    proj, use = CFG["project"], spec.get("use")
    cmd = spec.get("run") or ((proj.get(use) or (proj.get("test", "") if use == "affected" else "")) if use else "")
    if not cmd and spec.get("section"):
        cmd = bullets(sec(ctx, spec)).get(spec.get("item", "명령"), "").strip("` ")
    if not cmd:
        if spec.get("optional"):
            return []
        where = f"'{spec.get('section')}' 절의 '{spec.get('item', '명령')}'" if spec.get("section") else "명령"
        return [f"{where}이 비었다" + (f"(config [project] {use}도 비었다)" if use else "")]
    cmd = cmd.replace("{files}", " ".join(plan_files(ctx["s"])))
    fail = spec.get("expect") == "fail"
    if fail:
        key = test_fingerprint(ctx, cmd)
        if key in ctx["p"].get("proven_fail", {}):
            return []
        prog = missing_program(cmd)
        if prog:
            return [f"`{cmd}`의 {prog}를 찾지 못했다. 테스트를 돌릴 수 있는 명령을 쓴다"]
    try:
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=spec.get("timeout", 600))
    except subprocess.TimeoutExpired:
        return [f"명령이 시간 안에 끝나지 않았다: {cmd}"]
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:])
    if fail and r.returncode in spec.get("reject", NOT_RUN):
        return [f"`{cmd}`가 테스트를 돌리지 못한 것으로 보인다(종료 코드 {r.returncode}). 테스트가 돌고 실패해야 한다. "
                f"이 도구에서 이 코드가 테스트 실패라면 검사의 reject를 바꾸는 제안서를 쓴다.\n{tail}"]
    if (r.returncode != 0) if fail else (r.returncode == 0):
        if fail and not dirty_code():
            ctx["p"].setdefault("proven_fail", {})[key] = now()
        return []
    want = "실패해야" if fail else "통과해야"
    return [f"`{cmd}`가 {want} 하는데 종료 코드가 {r.returncode}다.\n{tail}"]


@check
def run_record(ctx, spec):
    """gate run으로 예상 종료 시각과 근거를 적었다"""
    return [] if ctx["p"].get("run") else ["gate run 기록이 없다"]


@check
def commit_since(ctx, spec):
    """이 상태에 들어온 뒤 path를 바꾼 커밋이 있다. mention = true면 더한 줄에 계획 번호가 있다"""
    since = ctx["p"]["entered"][ctx["state"]]["commit"]
    diff = git("diff", "-U0", f"{since}..HEAD", "--", spec["path"])
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
    if not added:
        return [f"{ctx['state']}에 들어온 뒤 {spec['path']}를 바꾼 커밋이 없다"]
    if spec.get("mention") and not any(ctx["pid"] in l for l in added):
        return [f"{ctx['state']}에 들어온 뒤 {spec['path']}에 {ctx['pid']}를 쓴 줄을 더한 커밋이 없다"]
    return []


@check
def choice(ctx, spec):
    """결정 절의 고른 갈래가 결과별 다음 행동 표 첫 칸 하나를 그대로 담거나 escape 문구다.
    escape면 --from으로 이어진 새 계획이 있어야 한다"""
    name, item = spec.get("section", "결정"), spec.get("item", "고른 갈래")
    options, escape = spec.get("options", "결과별 다음 행동"), spec.get("escape", "예상 밖")
    chosen = bullets(ctx["s"].get(name, [])).get(item, "")
    if not chosen:
        return [f"'{name}' 절의 '{item}'이 비었다"]
    if escape and escape in chosen:
        if not any(q.get("from") == ctx["pid"] for q in ctx["flow"]["plans"].values()):
            return [f"'{escape}'이면 --from {ctx['pid']}로 이어진 새 계획이 있어야 한다"]
        return []
    t = tables(ctx["s"].get(options, []))
    bounds = [r[0] for r in (filled(t[0]) if t else [])]
    return [] if any(b in chosen for b in bounds) else [f"'{item}'에 '{options}' 표 첫 칸 하나를 그대로 쓰거나 '{escape}'을 쓴다"]


@check
def refs(ctx, spec):
    """절에서 pattern에 맞는 참조가 file에 모두 있다"""
    text = "\n".join(sec(ctx, spec))
    target = (ROOT / spec["file"]).read_text(encoding="utf-8") if (ROOT / spec["file"]).exists() else ""
    found = re.findall(spec["pattern"], text)
    miss = [x for x in found if x not in target]
    if spec.get("required") and not found:
        return [f"'{spec['section']}' 절에 {spec['pattern']} 형식의 참조가 없다"]
    return [f"{x}가 {spec['file']}에 없다" for x in miss]


@check
def paths_exist(ctx, spec):
    """절에 적은 파일 경로(/가 든 것)가 모두 있다. items를 주면 그 bullet 항목만 본다.
    required = true면 하나 이상 있어야 한다"""
    lines = sec(ctx, spec)
    if spec.get("items"):
        b = bullets(lines)
        lines = [b.get(k, "") for k in spec["items"]]
    text = re.sub(r"\w+://\S+", "", "\n".join(lines))  # URL은 보지 않는다
    found = re.findall(r"[\w.][\w./-]*/[\w./-]*\.\w+", text)
    miss = [f"{x}가 없다" for x in found if not (ROOT / x).exists()]
    if spec.get("required") and not found:
        miss.append(f"'{spec['section']}' 절에 파일 경로가 없다")
    return miss


@check
def reviewed(ctx, spec):
    """state에서 받은 검토 뒤 계획 내용이 바뀌지 않았다"""
    rev = ctx["p"].get("review", {}).get(spec["state"])
    if not rev or not rev["pass"] or rev["hash"] != plan_hash(ctx["text"], ctx["cfg"]):
        return [f"{spec['state']}에서 검토한 계획 내용이 바뀌었다. back으로 {spec['state']}에 돌아가 다시 검토를 받는다"]
    return []


@check
def unchanged(ctx, spec):
    """state를 넘긴 뒤 그 상태가 쓰는 절이 바뀌지 않았다. sections로 볼 절을, section과 items로 bullet 항목을 고른다.
    조건이 맞지 않아 건너뛴 상태는 보지 않는다"""
    p, st = ctx["p"], spec["state"]
    snap = p.get("passed", {}).get(st)
    if snap is None:
        return [] if st in p.get("skipped", []) else [f"{st}를 넘긴 기록이 없다. back으로 {st}에 돌아가 다시 넘긴다"]
    if spec.get("items"):
        keys = [f"{spec['section']}: {k}" for k in spec["items"]]
    else:
        keys = spec.get("sections") or ([spec["section"]] if spec.get("section") else
                                        ctx["cfg"]["states"][st].get("sections", []))
    cur = snapshot(ctx["s"])
    return [f"{st}를 넘긴 뒤 '{k}'이 바뀌었다. 되돌리거나 back으로 {st}에 돌아가 다시 넘긴다"
            for k in keys if snap.get(k) != cur.get(k)]


@check
def test_edits(ctx, spec):
    """state를 처음 넘긴 뒤(state가 없거나 건너뛰었으면 계획을 시작한 뒤) 고치거나 지운 테스트 파일([project] tests)이
    section 표의 첫 칸에 있다. 계획의 코드 파일만 보고, 새로 더한 테스트 파일은 보지 않는다"""
    p, st = ctx["p"], spec.get("state")
    base = p.get("first_passed", {}).get(st) if st else None
    after = f"{st}를 넘긴 뒤" if base else "계획을 시작한 뒤"
    base = base or p["entered"]["_start"]["commit"]
    listed = set(plan_files(ctx["s"]))
    files = [f for f in git("diff", "--name-only", "--diff-filter=DMRT", f"{base}..HEAD").splitlines()
             if is_test(f) and f in listed]
    noted = {r[0].strip("` ") for t in tables(sec(ctx, spec)) for r in filled(t)}
    return [f"{f}는 {after} 고치거나 지운 테스트 파일이다. '{spec['section']}' 표에 파일과 고친 이유를 쓴다"
            for f in files if f not in noted]


def applies(spec, kind):
    return (not spec.get("only") or kind in spec["only"]) and kind not in spec.get("skip", [])


def run_checks(ctx):
    st = ctx["cfg"]["states"][ctx["state"]]
    miss = []
    for spec in st.get("checks", []):
        if not applies(spec, ctx["p"]["kind"]):
            continue
        fn = CHECKS.get(spec["check"])
        if fn is None:
            raise SystemExit(f"config.toml: 검사 '{spec['check']}'가 없다(있는 것: {', '.join(CHECKS)})")
        miss += fn(ctx, spec)
    if not miss and st.get("review") and applies(st.get("review_when", {}), ctx["p"]["kind"]):
        miss += review_check(ctx, st)
    return miss


# 판단 검토

REVIEW_SCHEMA = json.dumps({"type": "object", "properties": {
    "pass": {"type": "boolean"}, "missing": {"type": "array", "items": {"type": "string"}}},
    "required": ["pass", "missing"]})


def plan_diff(ctx):
    """계획을 시작한 뒤의 코드 diff. 머리에 '변경 파일' 표와 다른 파일을 적는다"""
    p, files_section = ctx["p"], ctx["cfg"]["project"]["files_section"]
    start = p["entered"]["_start"]["commit"]
    listed, changed = plan_files(ctx["s"]), changed_since(start)
    code = sorted(f for f in changed if is_code(f))
    target = listed or code
    head = [f"# {ctx['pid']} 코드 diff ({start}..HEAD, 계획을 시작한 뒤의 커밋, '{files_section}' 표의 파일)",
            f"# 표에 있는데 바뀌지 않은 파일: {', '.join(f for f in listed if f not in changed) or '없음'}",
            f"# 표에 없는데 바뀐 코드 파일(다른 계획의 변경일 수 있다): {', '.join(f for f in code if f not in listed) or '없음'}",
            ""]
    return "\n".join(head) + (git("diff", f"{start}..HEAD", "--", *target) if target else "")


def review_check(ctx, st):
    """검토자 호출. review에 {diff}가 있으면 계획을 시작한 뒤의 코드 diff를 파일로 주고, diff가 바뀌어도 다시 검토한다"""
    p, state, text = ctx["p"], ctx["state"], ctx["text"]
    diff = plan_diff(ctx) if "{diff}" in st["review"] else ""
    dh = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16] if diff else None
    rev = p.get("review", {}).get(state)
    if rev and rev["hash"] == plan_hash(text, ctx["cfg"]) and rev["pass"] and rev.get("diff") == dh:
        return []
    model = st.get("review_model") or CFG["review"].get("model")
    if not ctx.get("allow_review"):
        return [f"검토자 호출 1회({model or '기본 모델'}, 약 1분)가 필요하다. next {ctx['pid']} --review로 다시 실행한다"]
    fields = {"plan": p["plan"], "title": p["title"], "kind": p["kind"], "state": state}
    if diff:
        f = git_path(f"gate-review/{ctx['pid']}.diff")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(diff, encoding="utf-8")
        try:
            fields["diff"] = f.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            fields["diff"] = f.resolve().as_posix()
    result, meta = call_reviewer(st["review"].format(**fields), model)
    calls, spent = (rev or {}).get("calls", 1 if rev else 0), (rev or {}).get("spent", (rev or {}).get("cost") or 0)
    p.setdefault("review", {})[state] = {"pass": bool(result["pass"]), "hash": plan_hash(text, ctx["cfg"]),
                                         "missing": result["missing"], "at": now(), **meta, "calls": calls + 1,
                                         "spent": round(spent + (meta.get("cost") or 0), 6)}
    if dh:
        p["review"][state]["diff"] = dh
    return [] if result["pass"] else [f"검토: {m}" for m in result["missing"]] or ["검토: FAIL"]


def call_reviewer(prompt, model=None):
    """읽기 도구만 쓰는 별도 Claude 호출. ({pass, missing}, {session, cost, model}). 호출이 실패하면 FAIL.
    사용자 설정을 읽지 않으므로 추론 단계는 [review] effort로 넘기고, MCP 서버와 skill 목록은 싣지 않는다"""
    prompt += (" 문제마다 file:line 근거를 붙여 missing에 한국어 한 문장씩 쓴다. 문제가 없으면 pass를 true로 둔다. "
               "파일을 고치지 않는다.")
    effort = CFG["review"].get("effort")
    err = ""
    try:
        r = subprocess.run([shutil.which("claude") or "claude", "-p", *(["--model", model] if model else []),
                            *(["--effort", effort] if effort else []), "--strict-mcp-config", "--disable-slash-commands",
                            "--setting-sources", "local", "--tools", CFG["review"].get("tools", "Read,Grep,Glob"),
                            "--no-session-persistence", "--output-format", "json", "--json-schema", REVIEW_SCHEMA],
                           cwd=ROOT, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=CFG["review"].get("timeout", 600), env=os.environ | {"GATE_REVIEW": "1"})
        err = r.stderr.strip()[-200:]
        out = json.loads(r.stdout)
        return out["structured_output"], {"session": out.get("session_id"), "cost": out.get("total_cost_usd"),
                                          "model": f"{model or '기본'}/{effort or '기본'}"}
    except Exception as e:
        return {"pass": False, "missing": [f"검토 호출 실패: {e!r}" + (f" ({err})" if err else "")]}, {}
