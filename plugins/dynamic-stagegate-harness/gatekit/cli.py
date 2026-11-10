"""gate 명령. 프로젝트 루트에서 python -X utf8 .gate/gate.py <명령>으로 부른다."""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from . import evolve
from .checks import run_checks
from .core import (CFG, CLOSED, P, PLANS, ROOT, Lock, cfg_for, git, head, is_code, is_harness, load, next_state, now,
                   parse_config, plan_files, plan_text, save, sections, state_def, structure_problems, template_text)

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
    if allowed is not None and a.kind not in allowed:
        sys.exit(f"{flow['stage']}단계에서는 {a.kind}를 시작할 수 없다(허용: {', '.join(sorted(allowed))})")
    opts = [x for x in (a.with_ or "").replace(",", " ").split() if x]
    bad = [x for x in opts if x not in kinds[a.kind].get("options", [])]
    if bad:
        sys.exit(f"{a.kind}에서 켤 수 있는 선택은 {', '.join(kinds[a.kind].get('options', [])) or '없음'}이다(받은 것: {', '.join(bad)})")
    if a.from_ and a.from_ not in flow["plans"]:
        sys.exit(f"{a.from_}가 없다")
    if not head():
        sys.exit("커밋이 하나도 없다. 설치 파일을 먼저 커밋한다")
    prefix = P["plan_prefix"]
    nums = [int(k[len(prefix):]) for k in flow["plans"] if k.startswith(prefix) and k[len(prefix):].isdigit()]
    pid = f"{prefix}{max(nums + [0]) + 1:03d}"
    PLANS.mkdir(parents=True, exist_ok=True)
    plan = (PLANS / f"{pid}.md").relative_to(ROOT).as_posix()
    tpl = template_text(CFG, a.kind)
    tpl = re.sub(r"^# \S+ 제목", lambda _: f"# {pid} {a.title}", tpl, count=1, flags=re.M)
    for k, v in (("종류", a.kind), ("선택", ", ".join(opts)), ("이어진 계획", a.from_ or "")):
        tpl = re.sub(rf"^- {k}:.*$", lambda _: f"- {k}: {v}".rstrip(), tpl, count=1, flags=re.M)
    (ROOT / plan).write_text(tpl, encoding="utf-8")
    p = {"title": a.title, "kind": a.kind, "from": a.from_, "plan": plan, "version": CFG["version"], "options": opts}
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
    if not miss:
        ctx = {"pid": pid, "p": p, "text": text, "s": s, "flow": flow, "state": state, "cfg": cfg_for(p),
               "allow_review": a.review}
        miss = run_checks(ctx)
    if miss:
        save(flow)  # 검토 결과는 남긴다
        print(f"{pid} {state}에서 넘어가지 못했다. 빠진 것:")
        for m in miss:
            print("  - " + m)
        sys.exit(1)
    nxt, _ = next_state(cfg_for(p), p, s, state)
    if nxt != "DONE" and p.get("visits", {}).get(nxt, 0) >= P["max_visits"]:
        save(flow)
        sys.exit(f"{pid}가 {nxt}에 {P['max_visits']}번 들어왔다. 사용자에게 방향을 묻고, 그만두면 abandon한다")
    nxt, skipped = advance(p, s, state, "next")
    save(flow)
    if skipped:
        print(f"조건이 맞지 않아 건너뜀: {', '.join(skipped)}")
    if nxt == "DONE":
        print(f"{pid} {p['title']}: 모든 상태를 지났다")
    else:
        show_todo(pid, p)


def cmd_back(a, flow):
    p = get_open(flow, a.pid)
    order = cfg_for(p)["kinds"][p["kind"]]["states"]
    if a.state not in order or order.index(a.state) >= order.index(p["state"]):
        sys.exit(f"{a.state}는 {p['state']}보다 앞 상태가 아니다")
    if p.get("visits", {}).get(a.state, 0) >= P["max_visits"]:
        sys.exit(f"{a.pid}가 {a.state}에 {P['max_visits']}번 들어왔다. 사용자에게 방향을 묻고, 그만두면 abandon한다")
    enter(p, a.state, f"back: {a.why}")
    save(flow)
    show_todo(a.pid, p)


def cmd_run(a, flow):
    p = get_open(flow, a.pid)
    if not state_def(p).get("run"):
        sys.exit(f"{a.pid}의 {p['state']}는 실행 상태가 아니다(config.toml의 run = true)")
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", a.eta) or not a.basis.strip():
        sys.exit("--eta HH:MM과 --basis 근거가 필요하다")
    p["run"] = {"eta": a.eta, "basis": a.basis, "at": now(), "ts": time.time(), "commit": head()}
    save(flow)
    print(f"{a.pid} 실행 기록: {a.eta}쯤 끝남({a.basis})")


def cmd_abandon(a, flow):
    p = get_open(flow, a.pid)
    if not a.why.strip():
        sys.exit("--why 이유가 필요하다")
    enter(p, "ABANDONED", f"abandon: {a.why}")
    save(flow)
    print(f"{a.pid} {p['title']}: 그만둠({a.why})")


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
    code = [f for f in staged() if f and is_code(f)]
    if code:
        allowed = set()
        for p in flow["plans"].values():
            if p["state"] not in CLOSED and state_def(p).get("code"):
                allowed |= set(plan_files(sections(plan_text(p))))
        fail += [f"{f}: 코드를 고치는 상태인 계획의 '{P['files_section']}' 절에 없는 파일이다" for f in code
                 if f not in allowed]
    for cmd in P["pre_commit"]:
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
    bad = evolve.lint(cfg)
    for b in bad:
        print("실패", b)
    print(f"lint: 문제 {len(bad)}건")
    sys.exit(1 if bad else 0)


def cmd_describe(a, flow):
    print(evolve.mermaid(CFG) if a.mermaid else evolve.describe(CFG))


def cmd_diagnose(a, flow):
    print(evolve.diagnose(flow))
    why = evolve.review_due(flow)
    if why:
        print("\n하네스 점검을 권하는 이유: " + "; ".join(why))


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
    print("  하네스 커밋: git add .gate/config.toml .gate/history, git commit -m \"harness: "
          f"{a.hid} 구조 변경\"")
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
            "check-proposal": cmd_check_proposal, "adopt": cmd_adopt}
    if a.cmd in ("status", "git-pre-commit", "git-commit-msg", "lint", "describe", "diagnose"):
        cmds[a.cmd](a, load())
        return
    with Lock():
        cmds[a.cmd](a, load())
