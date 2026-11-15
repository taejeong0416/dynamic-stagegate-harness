"""gate 명령. 프로젝트 루트에서 python -X utf8 .gate/gate.py <명령>으로 부른다."""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from . import evolve, records, score
from .checks import run_checks
from .core import (CFG, CLOSED, ENGINE, P, PLANS, ROOT, Lock, bullets, cfg_for, cond_holds, git, head, is_code_any,
                   is_harness, is_test,
                   load, log_event, next_state, now, parse_config, plan_files, plan_text, save, sections, snapshot,
                   state_def, structure_problems, template_text)

CLI = "python -X utf8 .gate/gate.py"


def enter(p, state, note=""):
    p["state"] = state
    p.pop("hold", None)
    p.setdefault("visits", {})[state] = p.get("visits", {}).get(state, 0) + 1
    rec = {"at": now(), "ts": time.time(), "commit": head()}
    p.setdefault("entered", {})[state] = rec
    p.setdefault("first", {}).setdefault(state, rec)
    p.setdefault("history", []).append([state, now(), rec["commit"], note])


def todo(p):
    return state_def(p).get("todo", [])


def show_todo(pid, p):
    print(f"{pid} {p['title']}: {p['state']} ({p['kind']}, 계획 파일 {p['plan']})")
    for t in todo(p):
        print("  - " + t)


def get_open(flow, pid):
    p = flow["plans"].get(pid)
    if not p or p["state"] in CLOSED:
        sys.exit(f"진행 중인 {pid}가 없다")
    return p


def stage_kinds(flow):
    for st in CFG["stages"]:
        if st["n"] == flow.get("stage"):
            return set(st["kinds"])
    return None


def limit_problem(p, s):
    """종류의 limit 조건을 벗어났으면 그 이유"""
    cfg = cfg_for(p)
    limit = cfg["kinds"][p["kind"]].get("limit")
    if limit and not cond_holds(limit, p, s, cfg):
        return (f"{p['kind']} 종류의 상한({evolve.fmt_cond(limit)})을 벗어났다. 사용자에게 알리고, 이 계획을 abandon한 뒤 "
                "맞는 종류로 start --from해서 잇는다")
    return None


def advance(p, s, after, note):
    """조건이 맞는 다음 상태로 옮긴다. 건너뛴 상태는 기록한다"""
    nxt, skipped = next_state(cfg_for(p), p, s, after)
    for st in skipped:
        p.setdefault("skipped", []).append(st)
        p.setdefault("history", []).append([st, now(), head(), "skip: 조건이 맞지 않음"])
    enter(p, nxt, note)
    return nxt, skipped


def cmd_start(a, flow):
    kinds = CFG["kinds"]
    if a.kind not in kinds:
        sys.exit(f"종류는 {', '.join(kinds)} 중 하나다")
    allowed = stage_kinds(flow)
    if allowed is not None and a.kind not in allowed and not kinds[a.kind].get("any_stage"):
        sys.exit(f"{flow['stage']}단계에서는 {a.kind}를 시작할 수 없다(허용: {', '.join(sorted(allowed))})")
    opts = [x for x in (a.with_ or "").replace(",", " ").split() if x]
    bad = [x for x in opts if x not in kinds[a.kind].get("options", [])]
    if bad:
        sys.exit(f"{a.kind}에서 켤 수 있는 선택은 {', '.join(kinds[a.kind].get('options', [])) or '없음'}이다(받은 것: {', '.join(bad)})")
    known = records.all_plans(flow)
    if a.from_ and a.from_ not in known:
        sys.exit(f"{a.from_}가 없다")
    if not head():
        sys.exit("커밋이 하나도 없다. 설치 파일을 먼저 커밋한다")
    prefix = P["plan_prefix"]
    nums = [int(k[len(prefix):]) for k in known if k.startswith(prefix) and k[len(prefix):].isdigit()]
    pid = f"{prefix}{max(nums + [0]) + 1:03d}"
    PLANS.mkdir(parents=True, exist_ok=True)
    plan = (PLANS / f"{pid}.md").relative_to(ROOT).as_posix()
    tpl = template_text(CFG, a.kind)
    tpl = re.sub(r"^# \S+ 제목", lambda _: f"# {pid} {a.title}", tpl, count=1, flags=re.M)
    for k, v in (("종류", a.kind), ("선택", ", ".join(opts)), ("이어진 계획", a.from_ or "")):
        tpl = re.sub(rf"^- {k}:.*$", lambda _: f"- {k}: {v}".rstrip(), tpl, count=1, flags=re.M)
    (ROOT / plan).write_text(tpl, encoding="utf-8")
    p = {"title": a.title, "kind": a.kind, "from": a.from_, "plan": plan, "version": CFG["version"], "engine": ENGINE,
         "options": opts}
    first_rec = {"at": now(), "ts": time.time(), "commit": head()}
    p["entered"] = {"_start": first_rec}
    advance(p, sections(tpl), None, "start")
    flow["plans"][pid] = p
    save(flow)
    show_todo(pid, p)


def cmd_next(a, flow):
    pid, p = a.pid, get_open(flow, a.pid)
    state = p["state"]
    text = plan_text(p)
    s = sections(text)
    miss = structure_problems(p, text)
    if not miss and limit_problem(p, s):
        miss = [limit_problem(p, s)]
    ctx = {"pid": pid, "p": p, "text": text, "s": s, "flow": flow, "state": state, "cfg": cfg_for(p),
           "allow_review": a.review}
    if not miss:
        miss = run_checks(ctx)
    if miss:
        save(flow)  # 검토 결과는 남긴다
        print(f"{pid} {state}에서 넘어가지 못했다. 빠진 것:")
        for m in miss:
            print("  - " + m)
            if not m.startswith("검토자 호출"):  # 하네스 점검 자료. --review로 다시 부르라는 안내는 뺀다
                log_event("next", m.splitlines()[0], plan=pid, plan_kind=p["kind"], state=state,
                          part=ctx.get("blame", {}).get(m, ""))
        sys.exit(1)
    log_event("pass", "", plan=pid, state=state)  # 앞에서 막힌 기록의 판정에 쓴다
    if state not in p.get("frozen", []):  # gate run 뒤에는 앞 상태의 스냅샷을 바꾸지 않는다(unchanged가 쓴다)
        p.setdefault("passed", {})[state] = snapshot(s)
    p.setdefault("first_passed", {}).setdefault(state, head())  # back으로 지우지 않는다. test_edits가 쓴다
    nxt, _ = next_state(cfg_for(p), p, s, state)
    cap = cfg_for(p)["project"]["max_visits"]
    if nxt != "DONE" and p.get("visits", {}).get(nxt, 0) >= cap:
        save(flow)
        sys.exit(f"{pid}가 {nxt}에 {cap}번 들어왔다. 사용자에게 방향을 묻고, 그만두면 abandon한다")
    nxt, skipped = advance(p, s, state, "next")
    if nxt == "DONE":
        closed(flow)
    save(flow)
    if skipped:
        print(f"조건이 맞지 않아 건너뜀: {', '.join(skipped)}")
    if nxt == "DONE":
        print(f"{pid} {p['title']}: 모든 상태를 지났다")
        if records.REC["index"]:
            print(f"  계획 색인 {records.REC['index']}에 행을 더했다. 상태 파일과 함께 커밋한다")
    else:
        show_todo(pid, p)


def closed(flow):
    """계획이 닫히면 오래된 닫힌 계획을 보관 파일로 옮기고 색인을 다시 쓴다"""
    moved = records.settle(flow)
    if moved:
        print(f"닫힌 계획 {', '.join(moved)}를 .gate/state-archive.jsonl로 옮겼다. 상태 파일과 함께 커밋한다")


def freeze(p):
    """이미 넘긴 상태의 스냅샷을 고정한다. 실행 결과가 보이기 시작하면 back으로 다시 넘겨도 unchanged의 기준이
    바뀌지 않아, 결과를 본 뒤 판정 경계나 eval 케이스를 고쳐 통과하지 못한다"""
    p["frozen"] = sorted(set(p.get("frozen", [])) | set(p.get("passed", {})))


def results_seen(p):
    """실행 상태에서 결과 파일(results 검사가 보는 로컬 파일) 하나라도 이 상태에 들어온 뒤에 생겼는가"""
    st = state_def(p)
    if not st.get("run"):
        return False
    if p.get("run"):
        return True
    since = p["entered"][p["state"]]["ts"]
    s = sections(plan_text(p))
    for spec in st.get("checks", []):
        if spec.get("check") == "results":
            paths = bullets(s.get(spec.get("section", "실행 계획"), [])).get(spec.get("item", "결과 파일"), "")
            if any((ROOT / x).exists() and (ROOT / x).stat().st_mtime >= since for x in paths.replace(",", " ").split()):
                return True
    return False


def cmd_back(a, flow):
    p = get_open(flow, a.pid)
    order = cfg_for(p)["kinds"][p["kind"]]["states"]
    if a.state not in order or order.index(a.state) >= order.index(p["state"]):
        sys.exit(f"{a.state}는 {p['state']}보다 앞 상태가 아니다")
    allowed = cfg_for(p)["kinds"][p["kind"]].get("back", {}).get(p["state"])
    if allowed is not None and a.state not in allowed:
        where = f"{', '.join(allowed)}로만 돌아갈 수 있다" if allowed else "돌아갈 수 없다"
        sys.exit(f"{p['kind']}의 {p['state']}에서는 {where}(config.toml의 back). 다른 경로가 필요하면 구조 변경 "
                 "제안서를 쓴다")
    cap = cfg_for(p)["project"]["max_visits"]
    if p.get("visits", {}).get(a.state, 0) >= cap:
        sys.exit(f"{a.pid}가 {a.state}에 {cap}번 들어왔다. 사용자에게 방향을 묻고, 그만두면 abandon한다")
    if results_seen(p):  # gate run 없이 돌린 결과도 이미 나왔으면 고정한다
        freeze(p)
    inc = score.add_incident(flow, a.defect, a.why, plan=a.pid, state=p["state"], source="back")
    for st in order[order.index(a.state):]:
        if st not in p.get("frozen", []):
            p.get("passed", {}).pop(st, None)
    enter(p, a.state, f"back: {a.why}")
    save(flow)
    print(f"사고 {inc['id']}({a.defect})로 남겼다")
    show_todo(a.pid, p)


def cmd_run(a, flow):
    p = get_open(flow, a.pid)
    if not state_def(p).get("run"):
        sys.exit(f"{a.pid}의 {p['state']}는 실행 상태가 아니다(config.toml의 run = true)")
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", a.eta) or not a.basis.strip():
        sys.exit("--eta HH:MM과 --basis 근거가 필요하다")
    p["run"] = {"eta": a.eta, "basis": a.basis, "at": now(), "ts": time.time(), "commit": head()}
    freeze(p)
    save(flow)
    print(f"{a.pid} 실행 기록: {a.eta}쯤 끝남({a.basis})")


def cmd_abandon(a, flow):
    p = get_open(flow, a.pid)
    if not a.why.strip():
        sys.exit("--why 이유가 필요하다")
    enter(p, "ABANDONED", f"abandon: {a.why}")
    closed(flow)
    save(flow)
    print(f"{a.pid} {p['title']}: 그만둠({a.why})")


def cmd_incident(a, flow):
    """통과한 뒤에 드러난 결함을 남긴다. 결과 판정이 무효이거나 사용자가 결함을 지적했을 때 쓴다"""
    if a.plan and a.plan not in records.all_plans(flow):
        sys.exit(f"{a.plan}가 없다")
    if len(a.note.strip()) < 5:
        sys.exit("--note에 무슨 결함이 어느 상태를 지나 드러났는지 쓴다")
    quote = " ".join((a.quote or "").split())
    inc = score.add_incident(flow, a.defect, a.note, plan=a.plan or "", state=a.state or "",
                             source="user" if quote else "result", quote=quote)
    save(flow)
    print(f"사고 {inc['id']}({a.defect})를 남겼다. diagnose의 부품 성적과 제안 초안에 쓰인다")


def cmd_records(a, flow):
    print("\n".join(records.report(flow)))


def cmd_hold(a, flow):
    p = get_open(flow, a.pid)
    q = " ".join(a.quote.split())
    if len(q) < 4:
        sys.exit("--quote에 사용자가 이번 턴에 멈추라고 쓴 문장을 그대로 넣는다")
    p["hold"] = {"quote": q, "at": now()}
    save(flow)
    print(f"{a.pid} 멈춤: 사용자 입력에 \"{q}\"가 든 턴에서는 stop hook이 이 계획의 상태로 막지 않는다")


def cmd_stage(a, flow):
    ns = [st["n"] for st in CFG["stages"]]
    if a.n not in ns:
        sys.exit(f"단계는 {', '.join(map(str, ns))} 중 하나다")
    if P["decisions_file"]:
        title = a.decision.strip()
        text = (ROOT / P["decisions_file"]).read_text(encoding="utf-8")
        if len(title) < 5 or title not in [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]:
            sys.exit(f"{P['decisions_file']}에 제목이 정확히 '{title}'인 항목이 없다. 사용자와 정한 결정을 먼저 쓴다")
    print(f"{flow.get('stage')}단계 → {a.n}단계")
    flow["stage"] = a.n
    flow.setdefault("stage_history", []).append([a.n, now(), a.decision])
    save(flow)


def status_text(flow):
    lines = [f"구조 버전 {CFG['version']}"]
    if CFG["stages"]:
        name = next((st.get("name", "") for st in CFG["stages"] if st["n"] == flow.get("stage")), "")
        lines.append(f"프로젝트 {flow.get('stage')}단계 {name}".rstrip())
    active = {k: v for k, v in flow["plans"].items() if v["state"] not in CLOSED}
    if not active:
        lines.append(f"진행 중인 계획 없음. 시작: {CLI} start --kind <{'|'.join(CFG['kinds'])}> --title \"...\"")
    for pid, p in active.items():
        ver = p.get("version", CFG["version"])
        tag = f", 구조 v{ver}" if ver != CFG["version"] else ""
        if p.get("engine", ENGINE) != ENGINE:
            tag += f", 엔진 바뀜({p['engine']}에서 {ENGINE})"
        lines.append(f"{pid} {p['title']}: {p['state']} ({p['kind']}{tag}, 계획 파일 {p['plan']})")
        lines += ["  - " + t for t in todo(p)]
        if p.get("run"):
            lines.append(f"  실행 기록: {p['run']['eta']}쯤 끝남({p['run']['basis']})")
    return "\n".join(lines)


def cmd_status(a, flow):
    print(status_text(flow))


def staged():
    return git("diff", "--cached", "--name-only").split("\n")


def cmd_pre_commit(a, flow):
    fail = []
    # 지금 구조나 진행 중인 계획의 구조 버전 중 하나라도 코드로 보는 파일. 허용 파일은 계획마다 자기 버전으로 뽑는다
    code = [f for f in staged() if f and is_code_any(f, flow)]
    if code:
        allowed, tests_state = set(), {}
        for pid, p in flow["plans"].items():
            if p["state"] not in CLOSED and state_def(p).get("code"):
                s, cfg = sections(plan_text(p)), cfg_for(p)
                files = plan_files(s, cfg)
                if state_def(p).get("edit") == "tests":
                    tests_state |= {f: f"{pid} {p['state']}" for f in files if not is_test(f, cfg)}
                    files = [f for f in files if is_test(f, cfg)]
                allowed |= set(files)
                if set(code) & set(files) and limit_problem(p, s):
                    fail.append(f"{pid}: {limit_problem(p, s)}")
        for f in code:
            if f in allowed:
                continue
            fail.append(f"{f}: {tests_state[f]}에서는 테스트 파일만 커밋한다" if f in tests_state else
                        f"{f}: 코드를 고치는 상태인 계획의 '{P['files_section']}' 절에 없는 파일이다")
    for cmd in P["pre_commit"]:  # 커밋마다 한 번 도는 저장소 명령이라 지금 구조를 따른다
        r = subprocess.run(cmd, shell=True, cwd=ROOT)
        if r.returncode != 0:
            fail.append(f"pre_commit 명령 실패: {cmd}")
    for f in fail:
        print("실패", f)
    sys.exit(1 if fail else 0)


def cmd_commit_msg(a, flow):
    msg = Path(a.path).read_text(encoding="utf-8").strip()
    files = [f for f in staged() if f]
    harness = [f for f in files if is_harness(f)]
    fail = []
    if harness and len(harness) != len(files):
        fail.append("하네스 파일과 다른 파일이 한 커밋에 섞였다: " + ", ".join(sorted(set(files) - set(harness))))
    if harness and not msg.startswith(P["harness_prefix"]):
        fail.append(f"하네스 파일 커밋의 메시지는 '{P['harness_prefix']}'로 시작한다")
    for f in fail:
        print("실패", f)
    sys.exit(1 if fail else 0)


# 구조 점검

def cmd_lint(a, flow):
    cfg = parse_config(Path(a.file).read_text(encoding="utf-8")) if a.file else CFG
    bad = evolve.lint(cfg) + ([] if a.file else evolve.parts_problems())
    for b in bad:
        print("실패", b)
    print(f"lint: 문제 {len(bad)}건")
    sys.exit(1 if bad else 0)


def cmd_describe(a, flow):
    print(evolve.mermaid(CFG) if a.mermaid else evolve.describe(CFG))


def cmd_diagnose(a, flow):
    n = score.rotate()
    print(evolve.diagnose(flow))
    if n:
        print(f"\n막힌 기록 {n}줄을 .gate/scorecard.json으로 접었다. 하네스 커밋과 따로 커밋한다")
    why = evolve.review_due(flow)
    if why:
        print("\n하네스 점검을 권하는 이유: " + "; ".join(why))


def cmd_parts(a, flow):
    print("\n".join(evolve.parts_report()))


def cmd_propose(a, flow):
    d = evolve.propose(a.hid).relative_to(ROOT).as_posix()
    print(f"{d}에 지금 부품(parts)과 local.toml의 사본을 만들었다. 이 사본에서 부품을 더하고 빼고 고친 뒤 "
          f".gate/proposals/{a.hid}.md에 근거를 쓰고 {CLI} check-proposal {a.hid}를 돌린다. "
          "라이브러리 부품은 .gate/library/<이름> 폴더를 사본의 parts 아래에 복사해서 더한다.")


def cmd_check_proposal(a, flow):
    miss = evolve.check_proposal(flow, a.hid, a.review)
    save(flow)
    diag = (evolve.PROPOSALS / f"{a.hid}.diag.md").relative_to(ROOT).as_posix()
    if miss:
        print(f"{a.hid} 제안서가 통과하지 못했다(진단과 diff: {diag}). 빠진 것:")
        for m in miss:
            print("  - " + m)
        sys.exit(1)
    print(f"{a.hid} 제안서 통과(진단과 diff: {diag}). 사용자에게 바꾸는 것과 근거를 보이고 승인을 받은 뒤 "
          f"{CLI} adopt {a.hid} --approved \"<사용자가 승인한 문장>\"")


def cmd_adopt(a, flow):
    v = evolve.adopt(flow, a.hid, a.approved)
    save(flow)
    print(f"구조 버전 {v}를 적용했다. 진행 중인 계획은 시작할 때의 버전을 계속 쓴다.")
    files = ".gate/config.toml .gate/history" + (" .gate/parts .gate/local.toml .gate/parts.lock"
                                                 if evolve.PARTS.exists() else "")
    print(f"  하네스 커밋: git add -A {files}, git commit -m \"harness: {a.hid} 구조 변경\"")
    print("  그다음 상태 파일과 제안서를 일반 커밋으로 남긴다: git add .gate/state.json .gate/proposals")


def main(argv=None):
    ap = argparse.ArgumentParser(prog=CLI)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--kind", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--from", dest="from_")
    s.add_argument("--with", dest="with_", help="켤 선택 상태(쉼표로 여러 개)")
    n = sub.add_parser("next")
    n.add_argument("pid")
    n.add_argument("--review", action="store_true")
    b = sub.add_parser("back")
    b.add_argument("pid")
    b.add_argument("state")
    b.add_argument("--why", required=True)
    b.add_argument("--defect", required=True, choices=list(score.DEFECTS),
                   help="되돌아가는 원인의 분류: " + ", ".join(f"{k}({v})" for k, v in score.DEFECTS.items()))
    inc = sub.add_parser("incident")
    inc.add_argument("--defect", required=True, choices=list(score.DEFECTS))
    inc.add_argument("--note", required=True)
    inc.add_argument("--plan")
    inc.add_argument("--state", help="결함이 드러난 상태")
    inc.add_argument("--quote", help="사용자가 이번 턴에 결함을 지적한 문장")
    sub.add_parser("records")
    r = sub.add_parser("run")
    r.add_argument("pid")
    r.add_argument("--eta", required=True)
    r.add_argument("--basis", required=True)
    ab = sub.add_parser("abandon")
    ab.add_argument("pid")
    ab.add_argument("--why", required=True)
    h = sub.add_parser("hold")
    h.add_argument("pid")
    h.add_argument("--quote", required=True)
    g = sub.add_parser("stage")
    g.add_argument("n", type=int)
    g.add_argument("--decision", required=True)
    sub.add_parser("status")
    sub.add_parser("git-pre-commit")
    cm = sub.add_parser("git-commit-msg")
    cm.add_argument("path")
    li = sub.add_parser("lint")
    li.add_argument("--file")
    de = sub.add_parser("describe")
    de.add_argument("--mermaid", action="store_true", help="종류마다 상태 경로를 Mermaid 다이어그램으로 출력")
    sub.add_parser("diagnose")
    sub.add_parser("parts")
    pr = sub.add_parser("propose")
    pr.add_argument("hid")
    cp = sub.add_parser("check-proposal")
    cp.add_argument("hid")
    cp.add_argument("--review", action="store_true")
    ad = sub.add_parser("adopt")
    ad.add_argument("hid")
    ad.add_argument("--approved", required=True)
    a = ap.parse_args(argv)
    cmds = {"start": cmd_start, "next": cmd_next, "back": cmd_back, "run": cmd_run, "abandon": cmd_abandon,
            "hold": cmd_hold, "stage": cmd_stage, "status": cmd_status, "git-pre-commit": cmd_pre_commit,
            "git-commit-msg": cmd_commit_msg, "lint": cmd_lint, "describe": cmd_describe, "diagnose": cmd_diagnose,
            "check-proposal": cmd_check_proposal, "adopt": cmd_adopt, "parts": cmd_parts, "propose": cmd_propose, "incident": cmd_incident,
            "records": cmd_records}
    if a.cmd in ("status", "git-pre-commit", "git-commit-msg", "lint", "describe", "parts", "records"):
        cmds[a.cmd](a, load())
        return
    with Lock():
        cmds[a.cmd](a, load())
