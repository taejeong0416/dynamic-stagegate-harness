"""넘어가는 조건 검사 함수. config.toml의 [states.X] checks 목록에서 이름으로 부른다.

검사 함수는 (ctx, spec)을 받아 빠진 항목 문장 목록을 돌려준다. 빈 목록이면 통과다.
ctx: pid, p(계획 기록), text(계획 파일), s(절), flow(상태 파일), state(지금 상태), cfg(계획이 쓰는 구조 버전)
spec에 only = [종류들] 이나 skip = [종류들] 을 두면 그 종류에만 걸거나 그 종류는 뺀다.
"""
import json
import os
import re
import shutil
import subprocess

from .core import (CFG, ROOT, bullets, body, changed_since, dirty_code, filled, first, git, new_numbers, now,
                   num_value, plan_files, plan_hash, result_status, tables)

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


@check
def command(ctx, spec):
    """명령이 기대한 대로 끝난다. run에 명령을 주거나, use = "test"로 [project] test 명령을 쓰거나,
    section과 item으로 계획 파일에 적은 명령을 쓴다. use가 비어 있으면 계획 파일의 명령으로 넘어간다.
    expect = "fail"이면 실패해야 통과한다(버그 재현, 테스트 먼저 쓰기)"""
    cmd = spec.get("run") or (CFG["project"].get(spec["use"], "") if spec.get("use") else "")
    if not cmd and spec.get("section"):
        cmd = bullets(sec(ctx, spec)).get(spec.get("item", "명령"), "").strip("` ")
    if not cmd:
        where = f"'{spec.get('section')}' 절의 '{spec.get('item', '명령')}'" if spec.get("section") else "명령"
        return [f"{where}이 비었다" + (f"(config [project] {spec['use']}도 비었다)" if spec.get("use") else "")]
    try:
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=spec.get("timeout", 600))
    except subprocess.TimeoutExpired:
        return [f"명령이 시간 안에 끝나지 않았다: {cmd}"]
    ok = (r.returncode != 0) if spec.get("expect") == "fail" else (r.returncode == 0)
    if ok:
        return []
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:])
    want = "실패해야" if spec.get("expect") == "fail" else "통과해야"
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


def review_check(ctx, st):
    p, state, text = ctx["p"], ctx["state"], ctx["text"]
    rev = p.get("review", {}).get(state)
    if rev and rev["hash"] == plan_hash(text, ctx["cfg"]) and rev["pass"]:
        return []
    model = st.get("review_model") or CFG["review"].get("model")
    if not ctx.get("allow_review"):
        return [f"검토자 호출 1회({model or '기본 모델'}, 약 1분)가 필요하다. next {ctx['pid']} --review로 다시 실행한다"]
    prompt = st["review"].format(plan=p["plan"], title=p["title"], kind=p["kind"], state=state)
    result, meta = call_reviewer(prompt, model)
    p.setdefault("review", {})[state] = {"pass": bool(result["pass"]), "hash": plan_hash(text, ctx["cfg"]),
                                         "missing": result["missing"], "at": now(), **meta}
    return [] if result["pass"] else [f"검토: {m}" for m in result["missing"]] or ["검토: FAIL"]


def call_reviewer(prompt, model=None):
    """읽기 도구만 쓰는 별도 Claude 호출. ({pass, missing}, {session, cost}). 호출이 실패하면 FAIL"""
    prompt += (" 문제마다 file:line 근거를 붙여 missing에 한국어 한 문장씩 쓴다. 문제가 없으면 pass를 true로 둔다. "
               "파일을 고치지 않는다.")
    err = ""
    try:
        r = subprocess.run([shutil.which("claude") or "claude", "-p", *(["--model", model] if model else []),
                            "--setting-sources", "local", "--tools", CFG["review"].get("tools", "Read,Grep,Glob"),
                            "--no-session-persistence", "--output-format", "json", "--json-schema", REVIEW_SCHEMA],
                           cwd=ROOT, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=CFG["review"].get("timeout", 600), env=os.environ | {"GATE_REVIEW": "1"})
        err = r.stderr.strip()[-200:]
        out = json.loads(r.stdout)
        return out["structured_output"], {"session": out.get("session_id"), "cost": out.get("total_cost_usd")}
    except Exception as e:
        return {"pass": False, "missing": [f"검토 호출 실패: {e!r}" + (f" ({err})" if err else "")]}, {}
