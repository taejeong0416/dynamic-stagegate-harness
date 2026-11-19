"""넘어가는 조건 검사 함수. config.toml의 [states.X] checks 목록에서 이름으로 부른다.

검사 함수는 (ctx, spec)을 받아 빠진 항목 문장 목록을 돌려준다. 빈 목록이면 통과다.
ctx: pid, p(계획 기록), text(계획 파일), s(절), flow(상태 파일), state(지금 상태), cfg(계획이 쓰는 구조 버전).
통과했지만 알릴 것은 ctx["notes"]에 더하면 next가 출력한다.
spec에 only = [종류들] 이나 skip = [종류들] 을 두면 그 종류에만 걸거나 그 종류는 뺀다.
"""
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess

from .core import (CFG, CLOSED, GATE_DIR, ROOT, bullets, body, cfg_for, changed_since, dirty_code, filled, first, git, git_path,
                   is_code, is_test, new_numbers, now, num_value, plan_files, plan_hash, plan_text, result_paths, result_status,
                   sections, snapshot, tables)

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
def cell_max(ctx, spec):
    """절 표의 column번째 칸(0부터, 기본은 마지막 칸)이 행마다 max자 이하다. 검토자와 에이전트가 계획을 읽을 때마다
    긴 칸이 비용이 된다"""
    col, cap = spec.get("column", -1), spec["max"]
    return [f"'{spec['section']}' 표 '{r[0]}' 행의 칸이 {len(r[col])}자다(상한 {cap}자). 줄여 쓴다"
            for t in tables(sec(ctx, spec)) for r in t if -len(r) <= col < len(r) and len(r[col]) > cap]


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
    return [] if plan_files(ctx["s"], ctx["cfg"]) else [f"'{ctx['cfg']['project']['files_section']}' 절에 config의 code 패턴에 맞는 파일이 없다"]


@check
def files_changed(ctx, spec):
    """적은 코드 파일이 이 상태에 처음 들어온 뒤의 커밋에서 모두 바뀌었다. any = true면 하나 이상"""
    since = first(ctx["p"], ctx["state"])["commit"]
    files = plan_files(ctx["s"], ctx["cfg"])
    changed = changed_since(since, *files) if files else set()
    if spec.get("any"):
        return [] if changed else [f"적은 코드 파일 중 {ctx['state']}에 들어온 뒤 커밋에서 바뀐 것이 없다"]
    return [f"{f}가 {ctx['state']}에 들어온 뒤 커밋에서 바뀌지 않았다" for f in files if f not in changed]


@check
def clean(ctx, spec):
    """코드에 커밋하지 않은 변경이 없다"""
    d = dirty_code(ctx["cfg"])
    return [] if not d else ["커밋하지 않은 코드 변경이 있다: " + ", ".join(p for _, p in d[:8])]


@check
def numbers(ctx, spec):
    """이 상태에 처음 들어온 뒤 코드에 새로 들어간 숫자(0과 1 제외)가 근거 표의 첫 칸에 모두 있다"""
    name = spec.get("section", "값 근거")
    vals = {num_value(r[0]) for t in tables(ctx["s"].get(name, [])) for r in t if r and r[0]}
    since = first(ctx["p"], ctx["state"])["commit"]
    return [f"'{name}' 표의 값 칸에 없는 숫자 {n} ({where}). 값 칸에는 숫자 하나만 쓴다"
            for n, where in new_numbers(since, plan_files(ctx["s"], ctx["cfg"])).items() if num_value(n) not in vals]


@check
def sample(ctx, spec):
    """표본 실행 절의 명령, 결과 요약, 커밋이 있고, 커밋이 적은 코드 파일을 마지막으로 바꾼 커밋이다"""
    name = spec.get("section", "표본 실행")
    b = bullets(ctx["s"].get(name, []))
    files = plan_files(ctx["s"], ctx["cfg"])
    last = git("log", "-1", "--format=%h", "--", *files).strip() if files else ""
    c = b.get("커밋", "")
    if not b.get("명령") or not b.get("결과 요약") or not c or c[:7] != last[:7]:
        return [f"'{name}' 절의 명령, 결과 요약, 커밋(코드 파일을 마지막으로 바꾼 커밋 {last})이 맞지 않다"]
    return []


@check
def results(ctx, spec):
    """적은 결과 파일이 모두 있고 이 상태에 들어온 뒤에 만들어졌다(remote가 있으면 서버도 본다). 칸에서 경로만 고르고,
    <이름>이나 {k} 같은 틀 문자가 붙은 경로는 뺀다. early 항목(기본 "조기 종료 결과 파일")의 파일이 모두 새로 있으면
    나머지 결과 파일이 없어도 통과한다. 계획이 일찍 끝나는 갈래는 뒤 단계 파일을 만들지 않기 때문이다.
    서버 확인이 실패하면 그대로 막는다"""
    name, item, early_item = spec.get("section", "실행 계획"), spec.get("item", "결과 파일"), spec.get("early", "조기 종료 결과 파일")
    b = bullets(ctx["s"].get(name, []))
    paths, early = result_paths(b.get(item, "")), result_paths(b.get(early_item, ""))
    if not paths:
        return [f"'{name}' 절의 '{item}'에 확인할 경로가 없다(<이름>, {{k}} 같은 틀 문자가 붙은 경로는 빼고 본다)"]
    bad = [x for x in paths + early if x.startswith((CFG["project"]["plans_dir"].rstrip("/") + "/", ".gate/"))]
    if bad:
        return [f"결과 파일로 계획 문서를 쓸 수 없다: {x}" for x in bad]
    since = ctx["p"]["entered"][ctx["state"]]["ts"]
    miss = result_status(paths, since)
    if miss and early and not any(x.startswith("(서버 확인 실패") for x in miss) and not result_status(early, since):
        ctx.setdefault("notes", []).append(f"'{early_item}' {len(early)}개가 있어 나머지 결과 파일 {len(miss)}개 없이 "
                                           "넘어간다: " + "; ".join(miss))
        return []
    return miss


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
    files = sorted(f for f in changed_since(ctx["p"]["entered"]["_start"]["commit"]) if is_test(f, ctx["cfg"]))
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
    proj, use = ctx["cfg"]["project"], spec.get("use")
    cmd = spec.get("run") or ((proj.get(use) or (proj.get("test", "") if use == "affected" else "")) if use else "")
    if not cmd and spec.get("section"):
        cmd = bullets(sec(ctx, spec)).get(spec.get("item", "명령"), "").strip("` ")
    if not cmd:
        if spec.get("optional"):
            return []
        where = f"'{spec.get('section')}' 절의 '{spec.get('item', '명령')}'" if spec.get("section") else "명령"
        return [f"{where}이 비었다" + (f"(config [project] {use}도 비었다)" if use else "")]
    cmd = cmd.replace("{files}", " ".join(plan_files(ctx["s"], ctx["cfg"])))
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
        if fail and not dirty_code(ctx["cfg"]):
            ctx["p"].setdefault("proven_fail", {})[key] = now()
        return []
    want = "실패해야" if fail else "통과해야"
    return [f"`{cmd}`가 {want} 하는데 종료 코드가 {r.returncode}다.\n{tail}"]


@check
def code_unchanged(ctx, spec):
    """이 상태에 들어온 뒤 계획의 코드 파일에 커밋도, 커밋하지 않은 변경도 없다. 결과가 앞 상태에서 검사받은 코드에서
    나왔는지 본다. 바뀌었으면 코드 상태로 돌아가 다시 지난 뒤 다시 실행한다"""
    files = plan_files(ctx["s"], ctx["cfg"])
    if not files:
        return []
    since = ctx["p"]["entered"][ctx["state"]]["commit"]
    moved = sorted(changed_since(since, *files) | {f for _, f in dirty_code(ctx["cfg"]) if f in files})
    return [f"{ctx['state']}에 들어온 뒤 계획의 코드 파일 {', '.join(moved)}가 바뀌었다. 결과가 앞에서 검사받은 코드에서 "
            "나왔다고 볼 수 없어, 코드 상태로 back해 다시 지난 뒤 다시 실행한다"] if moved else []


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
    if st in p.get("frozen", []):  # gate run 뒤에는 결과가 보이므로 다시 넘겨도 기준을 바꾸지 않는다
        how = f"gate run 뒤라 {st}를 다시 넘겨도 기준이 그대로다. 되돌리고, 새 기준이 필요하면 start --from {ctx['pid']}로 잇는다"
    else:
        how = f"되돌리거나 back으로 {st}에 돌아가 다시 넘긴다"
    return [f"{st}를 넘긴 뒤 '{k}'이 바뀌었다. {how}" for k in keys if snap.get(k) != cur.get(k)]


@check
def test_edits(ctx, spec):
    """state를 처음 넘긴 뒤(state가 없거나 건너뛰었으면 계획을 시작한 뒤) 고치거나 지운 테스트 파일([project] tests)이
    section 표의 첫 칸에 있다. 계획의 코드 파일만 보고, 새로 더한 테스트 파일은 보지 않는다"""
    p, st = ctx["p"], spec.get("state")
    base = p.get("first_passed", {}).get(st) if st else None
    after = f"{st}를 넘긴 뒤" if base else "계획을 시작한 뒤"
    base = base or p["entered"]["_start"]["commit"]
    listed = set(plan_files(ctx["s"], ctx["cfg"]))
    files = [f for f in git("diff", "--name-only", "--diff-filter=DMRT", f"{base}..HEAD").splitlines()
             if is_test(f, ctx["cfg"]) and f in listed]
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
        got = fn(ctx, spec)
        ctx.setdefault("blame", {}).update({m: spec.get("part", "") for m in got})  # 막은 검사의 부품
        miss += got
    if not miss and st.get("review") and applies(st.get("review_when", {}), ctx["p"]["kind"]):
        got = review_check(ctx, st)
        ctx.setdefault("blame", {}).update({m: st.get("review_part", "") for m in got})
        miss += got
    return miss


# 기록 검사

@check
def doc_size(ctx, spec):
    """현재 상태 문서(path가 없으면 status_file)가 상한 글자 수(max가 없으면 [records] current_max) 안이다"""
    from .records import REC, read
    path = spec.get("path") or ctx["cfg"]["project"]["status_file"]
    n, cap = len(read(path)), spec.get("max", REC["current_max"])
    if not path or n <= cap:
        return []
    return [f"{path}가 {n}자다(상한 {cap}자). 닫힌 항목을 {REC['archive_file'] or '보관 문서'}로 옮긴다"]


@check
def field(ctx, spec):
    """문서(path가 없으면 status_file) 머리의 field에 값이 있다. after_last = {file, pattern}을 주면 그 값의 번호가
    file에서 pattern(번호를 괄호로 묶은 정규식)에 맞는 가장 큰 번호의 다음이다"""
    from .records import fields, read
    path = spec.get("path") or ctx["cfg"]["project"]["status_file"]
    val = fields(read(path)).get(spec["field"], "")
    if not val:
        return [f"{path} 머리의 '{spec['field']}' 필드가 비었다(첫 줄 ---와 다음 --- 사이에 '{spec['field']}: 값'으로 쓴다)"]
    rule = spec.get("after_last")
    if rule:
        want = max((int(n) for n in re.findall(rule["pattern"], read(rule["file"]))), default=0) + 1
        got = re.search(rule["pattern"], val)
        if not got or int(got.group(1)) != want:
            return [f"{path} 머리의 '{spec['field']}'가 {val}인데, {rule['file']}의 마지막 번호 다음은 {want}다"]
    return []


@check
def survey(ctx, spec):
    """정리 전의 문서 크기, 항목 ID, 풀리는 참조를 계획 기록에 남긴다. kept 검사가 정리 뒤와 견준다"""
    from .records import survey as measure
    ctx["p"]["survey"] = measure(ctx["flow"])
    return []


@check
def kept(ctx, spec):
    """survey 때 풀리던 참조가 지금도 풀리고, 그때 현재 상태 문서에 있던 항목 ID가 현재 상태 문서나 보관 문서에 있다"""
    from .records import REC, item_ids, read, resolves
    snap = ctx["p"].get("survey")
    if snap is None:
        return ["정리 전 측정 기록이 없다. back으로 측정 상태에 돌아가 다시 넘긴다"]
    proj = ctx["cfg"]["project"]
    ledger_text = "\n".join(read(x) for x in REC["ledgers"])
    have = item_ids(read(proj["status_file"])) | item_ids(read(REC["archive_file"]))
    miss = [f"정리 전에 풀리던 참조 {r}가 풀리지 않는다" for r in snap["refs"] if not resolves(r, ctx["flow"], ledger_text)]
    return miss + [f"정리 전 {proj['status_file']}에 있던 항목 {i}가 {proj['status_file']}에도 "
                   f"{REC['archive_file'] or '보관 문서'}에도 없다" for i in snap["items"] if i not in have]


# 판단 검토

REVIEW_SCHEMA = json.dumps({"type": "object", "properties": {
    "pass": {"type": "boolean"}, "missing": {"type": "array", "items": {"type": "string"}}},
    "required": ["pass", "missing"]})


def plan_diff(ctx, base=None):
    """계획의 코드 diff. base가 없으면 계획을 시작한 뒤, 있으면 base 뒤의 변경만 담는다.
    머리에 범위와, 계획을 시작한 뒤 '변경 파일' 표와 다르게 바뀐 파일을 적는다"""
    p, files_section = ctx["p"], ctx["cfg"]["project"]["files_section"]
    start = p["entered"]["_start"]["commit"]
    listed, changed = plan_files(ctx["s"], ctx["cfg"]), changed_since(start)
    code = sorted(f for f in changed if is_code(f, ctx["cfg"]))
    target = listed or code
    scope = f"{base}..HEAD, 앞 검토를 통과한 뒤의 변경만" if base else f"{start}..HEAD, 계획을 시작한 뒤의 커밋"
    head = [f"# {ctx['pid']} 코드 diff ({scope}, '{files_section}' 표의 파일)",
            f"# 표에 있는데 바뀌지 않은 파일: {', '.join(f for f in listed if f not in changed) or '없음'}",
            f"# 표에 없는데 바뀐 코드 파일(다른 계획의 변경일 수 있다): {', '.join(f for f in code if f not in listed) or '없음'}",
            ""]
    body_ = git("diff", f"{base or start}..HEAD", "--", *target) if target else ""
    return "\n".join(head) + body_, body_


def unreviewed_parent(ctx):
    """--from으로 이은 부모 계획이 같은 상태의 검토를 면제(가벼운 실행)로 건너뛰었으면 그 상태에서 바뀐 부모의 코드 diff.
    가벼운 실행의 결과가 이어진 계획의 무거운 실행에 쓰일 때 그 코드도 한 번은 검토받게 한다"""
    from .records import all_plans
    pid, st = ctx["p"].get("from") or "", ctx["state"]
    q = all_plans(ctx["flow"]).get(pid)
    if not q or not (q.get("review", {}).get(st) or {}).get("exempt"):
        return ""
    start = first(q, st)["commit"]
    end = q["history"][-1][2] if q["state"] in CLOSED else "HEAD"
    files = plan_files(sections(plan_text(q)), cfg_for(q))
    d = git("diff", f"{start}..{end}", "--", *files) if files else ""
    return (f"\n\n# 부모 계획 {pid}의 검토받지 않은 코드(가벼운 실행으로 {st} 검토를 건너뜀, {start}..{end}). 이 계획의 결과가 "
            f"이 코드에 기대므로 함께 본다\n{d}") if d.strip() else ""


CITE_DEFAULT = r"\b[A-Z]+\d{3}\b"  # 계획 번호(P001)와 같은 꼴의 참조


def superseded_heads(lines, heads, rec):
    """원장 문서에서 대체된 제목 줄 번호 → 대체한 항목. [records] supersedes는 새 항목 안에서 옛 항목을 가리키는 줄의
    정규식이고(그 줄의 따옴표 속 문구가 든 앞쪽 제목이 대체된다), superseded는 옛 항목이 스스로 대체됐다고 적는 줄의 정규식이다"""
    out = {}
    if not (rec.get("supersedes") or rec.get("superseded")):
        return out
    bounds = heads + [len(lines)]
    for k, h in enumerate(heads):
        title = lines[h].lstrip("#").strip()
        for line in lines[h + 1:bounds[k + 1]]:
            if rec.get("superseded") and re.search(rec["superseded"], line):
                out.setdefault(h, []).append(line.strip()[:80])
            if rec.get("supersedes") and re.search(rec["supersedes"], line):
                for ref in re.findall(r"\"([^\"]+)\"", line):
                    for h2 in heads[:k]:
                        if ref.strip() and ref.strip() in lines[h2]:
                            out.setdefault(h2, []).append(title[:60])
    return out


def context_file(ctx, st):
    """검토자가 먼저 읽을 발췌. 상태의 context에 적은 파일마다, context_full자 이하면 전문을 담는다. 넘으면 csv 머리 행,
    계획 파일이 인용한 참조([review] cite 패턴과 계획 번호)가 든 줄, 최근 [records] context_recent개 제목과 제목마다 첫 줄을
    담고, 원장의 대체된 항목은 제목과 대체한 항목만 담는다. 기록이 쌓여도 발췌는 인용한 양만큼만 커진다"""
    from .records import DEFAULTS
    rec = DEFAULTS | ctx["cfg"].get("records", {})
    text = ctx["text"]
    pats = ctx["cfg"]["review"].get("cite", [CITE_DEFAULT])
    cited = {ctx["pid"]} | {x for pat in pats for x in re.findall(pat, text)}
    out = [f"# {ctx['pid']} {ctx['state']} 검토 발췌", "",
           f"계획 파일이 인용한 참조: {', '.join(sorted(cited))}", ""]
    for rel in st.get("context", []):
        f = ROOT / rel
        if not f.exists():
            out += [f"## {rel}", "", "(없음)", ""]
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        if sum(len(l) + 1 for l in lines) <= ctx["cfg"]["review"].get("context_full", 6000):
            out += [f"## {rel} (전문)", ""] + lines + [""]
            continue
        heads = [i for i, l in enumerate(lines) if l.startswith("#")]
        sup = superseded_heads(lines, heads, rec) if rel in rec["ledgers"] else {}
        n = rec["context_recent"]
        recent = set(heads[-n:]) if n else set(heads)
        keep, after_head, skipped = [], False, 0
        for i, line in enumerate(lines):
            hit = any(re.search(rf"(?<![\w]){re.escape(c)}(?![\w])", line) for c in cited)
            head = line.startswith("#")
            if head and i in sup:
                keep.append(line[:400] + f" (대체됨: {'; '.join(sup[i])}. 남은 내용은 원본에서 Grep)")
                after_head = False
            elif (rel.endswith(".csv") and i == 0) or hit or (head and (i in recent or line.startswith("# "))):
                keep.append(line[:400])
                after_head = head
            elif head:
                skipped += 1
                after_head = False
            elif after_head and line.strip():
                keep.append(line[:200])
                after_head = False
        note = f", 인용되지 않은 앞 제목 {skipped}개는 뺐다" if skipped else ""
        out += [f"## {rel} (발췌: 인용한 참조가 든 줄, 최근 제목과 제목마다 첫 줄{note}. 나머지는 원본에서 Grep)", ""] + keep + [""]
    f = git_path(f"gate-review/{ctx['pid']}-{ctx['state']}-context.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(out), encoding="utf-8")
    return rel_path(f)


def rel_path(f):
    try:
        return f.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return f.resolve().as_posix()


# 재검토가 한 번에 한두 개씩 새 지적을 내면 같은 상태 검토가 여러 번 돈다(대회 하네스 P031 IDEATE 7회). 첫 검토에 모두
# 내게 하고, 재검토는 앞 지적과 그 뒤 바뀐 내용만 보며 앞 검토 때 있던 내용에는 결과 해석을 바꿀 결함만 새로 낸다
FIRST_REVIEW = " 이번 검토에서 빠진 것을 모두 한 번에 낸다. 다음 검토는 이번 지적이 고쳐졌는지와 고친 곳만 본다."
RE_REVIEW = (" 이 검토는 재검토다. (1) 앞 검토 지적이 고쳐졌는지와 (2) 고치며 바뀐 내용에서 생긴 문제를 본다. 앞 검토 때 이미 "
             "있던 내용에는 새 지적을 내지 않는다. 다만 누수, 판정 기준이나 결과별 다음 행동의 오류, 근거 없는 값을 근거 있는 "
             "것처럼 적은 것처럼 결과 해석을 바꿀 결함은 낸다.")
# 통과한 계획을 고쳐 다시 받는 검토(대회 하네스에서 back으로 PLAN에 온 16번이 모두 첫 검토를 다시 받았다)
CHANGE_REVIEW = (" 이 검토는 앞 검토를 통과한 계획을 고친 뒤의 검토다. 통과한 원문과의 diff {diff}를 먼저 읽고, 바뀐 내용 자체의 "
                 "문제와 그 변경 때문에 계획의 다른 절과 맞지 않게 된 곳만 본다. 바뀌지 않은 내용에는 결과 해석을 바꿀 결함만 낸다.")
# 검토 기록에 누적하는 지적 수와 지적 앞 표시. 검토가 결과를 바꾼 지적을 냈는지 부품 성적에서 본다
TAGS = {"defects": "[결함]", "formats": "[형식]"}


def review_check(ctx, st):
    """검토자 호출. review에 {diff}가 있으면 코드 diff를 파일로 준다. 같은 계획 내용으로 통과한 뒤에는 그 뒤에 바뀐
    코드만 다시 본다. 통과한 뒤 계획을 고쳤으면 통과한 원문과의 diff를 주고 바뀐 곳만 보게 한다. {context}가 있으면
    상태의 context 파일에서 뽑은 발췌를 준다. 상태의 review_exempt = {section, item} 항목이 "예"로 시작하면 부르지 않고
    면제를 검토 기록에 남긴다"""
    p, state, text = ctx["p"], ctx["state"], ctx["text"]
    rev = p.get("review", {}).get(state)
    same = bool(rev) and rev["hash"] == plan_hash(text, ctx["cfg"])
    uses_diff = "{diff}" in st["review"]
    base = rev.get("upto") if same and uses_diff else None
    if same and rev["pass"]:
        if not uses_diff:
            return []
        if base and not plan_diff(ctx, base)[1].strip():
            return []  # 통과한 뒤 코드가 바뀌지 않았다
    ex = st.get("review_exempt")
    light = bullets(ctx["s"].get(ex["section"], [])).get(ex["item"], "") if ex else ""
    if light.startswith("예"):  # 가벼운 실행은 검토 없이 넘기고, 그 주장이 맞는지는 앞 상태의 검토자가 본다
        p.setdefault("review", {})[state] = {"pass": True, "hash": plan_hash(text, ctx["cfg"]), "missing": [], "at": now(),
                                             "exempt": light, "model": "면제", "calls": (rev or {}).get("calls", 0),
                                             "spent": (rev or {}).get("spent", 0), "upto": git("rev-parse", "--short", "HEAD").strip(),
                                             **{k: (rev or {}).get(k, 0) for k in TAGS}}
        ctx.setdefault("notes", []).append(f"'{ex['item']}: {light}'이라 {state} 검토를 건너뛰었다(상태 파일의 검토 기록에 exempt로 남김)")
        return []
    model = st.get("review_model") or ctx["cfg"]["review"].get("model")
    if not ctx.get("allow_review"):
        return [f"검토자 호출 1회({model or '기본 모델'}, 약 1분)가 필요하다. next {ctx['pid']} --review로 다시 실행한다"]
    fields = {"plan": p["plan"], "title": p["title"], "kind": p["kind"], "state": state}
    if uses_diff:
        diff = plan_diff(ctx, base)[0] + ("" if base else unreviewed_parent(ctx))
        f = git_path(f"gate-review/{ctx['pid']}.diff")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(diff, encoding="utf-8")
        fields["diff"] = rel_path(f)
    if "{context}" in st["review"]:
        fields["context"] = context_file(ctx, st)
    prompt = st["review"].format(**fields)
    passed = git_path(f"gate-review/{ctx['pid']}-{state}-passed.md")
    passed.parent.mkdir(parents=True, exist_ok=True)
    if rev and not rev["pass"] and rev.get("missing"):
        prompt += RE_REVIEW + " 앞 검토 지적: " + " / ".join(rev["missing"])
    elif rev and rev["pass"] and not same and not rev.get("exempt") and passed.exists():
        f = git_path(f"gate-review/{ctx['pid']}-{state}-change.diff")
        f.write_text("\n".join(difflib.unified_diff(passed.read_text(encoding="utf-8").splitlines(), text.splitlines(),
                                                    "통과한 원문", "지금 계획", lineterm="")) + "\n", encoding="utf-8")
        prompt += CHANGE_REVIEW.format(diff=rel_path(f))
    else:  # 첫 검토. 통과한 원문이 없는 옛 기록과 면제로 넘긴 기록도 여기로 온다
        prompt += FIRST_REVIEW
    result, meta = call_reviewer(prompt, model, ctx["cfg"])
    calls, spent = (rev or {}).get("calls", 1 if rev else 0), (rev or {}).get("spent", (rev or {}).get("cost") or 0)
    tags = {k: (rev or {}).get(k, 0) + sum(m.lstrip().startswith(t) for m in result["missing"]) for k, t in TAGS.items()}
    p.setdefault("review", {})[state] = {"pass": bool(result["pass"]), "hash": plan_hash(text, ctx["cfg"]),
                                         "missing": result["missing"], "at": now(), **meta, "calls": calls + 1,
                                         "spent": round(spent + (meta.get("cost") or 0), 6), **tags}
    if result["pass"]:
        passed.write_text(text, encoding="utf-8")
    if uses_diff:  # 다음 검토의 diff 시작점. 실패하면 앞에서 통과한 시점을 그대로 둔다
        p["review"][state]["upto"] = git("rev-parse", "--short", "HEAD").strip() if result["pass"] else base
    return [] if result["pass"] else [f"검토: {m}" for m in result["missing"]] or ["검토: FAIL"]


def call_reviewer(prompt, model=None, cfg=None):
    """읽기 도구만 쓰는 별도 Claude 호출. ({pass, missing}, {session, cost, model}). 호출이 실패하면 FAIL.
    사용자 설정을 읽지 않으므로 추론 단계는 [review] effort로 넘기고, MCP 서버와 skill 목록은 싣지 않는다.
    cfg는 계획이 쓰는 구조 버전이고, 없으면 지금 구조(제안서 검토)다"""
    rv = (cfg or CFG)["review"]
    prompt += (" 문제마다 file:line 근거를 붙여 missing에 한국어 한 문장씩 쓴다. 문장 맨 앞에는 [결함](고치지 않으면 실행 결과, "
               "판정, 비교가 틀려지는 것)이나 [형식](기록 형식, 문구, 출처 표기)을 붙인다. 문제가 없으면 pass를 true로 둔다. "
               "파일을 고치지 않는다.")
    effort = rv.get("effort")
    err = ""
    try:
        r = subprocess.run([shutil.which("claude") or "claude", "-p", *(["--model", model] if model else []),
                            *(["--effort", effort] if effort else []), "--strict-mcp-config", "--disable-slash-commands",
                            "--setting-sources", "local", "--tools", rv.get("tools", "Read,Grep,Glob"),
                            "--no-session-persistence", "--output-format", "json", "--json-schema", REVIEW_SCHEMA],
                           cwd=ROOT, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=rv.get("timeout", 600), env=os.environ | {"GATE_REVIEW": "1"})
        err = r.stderr.strip()[-200:]
        out = json.loads(r.stdout)
        return out["structured_output"], {"session": out.get("session_id"), "cost": out.get("total_cost_usd"),
                                          "model": f"{model or '기본'}/{effort or '기본'}"}
    except Exception as e:
        return {"pass": False, "missing": [f"검토 호출 실패: {e!r}" + (f" ({err})" if err else "")]}, {}


def load_part_checks(folder=None):
    """부품 폴더의 checks.py를 읽어 검사를 더한다. 모듈은 from gatekit.checks import check로 등록한다"""
    import importlib.util
    for f in sorted((folder or GATE_DIR / "parts").glob("*/checks.py")):
        mod = importlib.util.spec_from_file_location("gate_part_" + re.sub(r"\W", "_", f.parent.name), f)
        mod.loader.exec_module(importlib.util.module_from_spec(mod))


load_part_checks()
