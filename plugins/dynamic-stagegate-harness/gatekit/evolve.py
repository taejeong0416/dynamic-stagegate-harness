"""하네스 구조 점검: lint, describe, diagnose, 제안서 검사, adopt.

구조를 바꾸는 길은 하나다. 에이전트가 .gate/proposals/H###.md에 새 config와 근거를 쓰고, check-proposal이 lint,
지난 기록 재생, diff를 붙여 검토자에게 보이고, 사용자가 승인한 문장을 adopt --approved에 넣는다(guard가 그 문장이
이번 턴 사용자 입력에 있는지 본다). 진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다.
"""
import difflib
import hashlib
import re
import shutil
import statistics
import time
from datetime import datetime

from .checks import CHECKS, call_reviewer
from .core import (CFG, CLOSED, GATE_DIR, ROOT, body, bullets, cfg_for, changed_since, git, head, is_code, now,
                   parse_config, read_events, sections, tables, template_text)

PROPOSALS = GATE_DIR / "proposals"
COND_KEYS = {"option", "changed", "max_files", "not", "any"}
MUST_PROTECT = (".gate/config.toml", ".gate/history/", ".gate/gatekit/")
# 검사가 section을 주지 않을 때 쓰는 기본 절
DEFAULT_SECTION = {"results": "실행 계획", "sample": "표본 실행", "choice": "결정", "numbers": "값 근거"}


# lint

def _conds(cond, where, kind_opts):
    out = []
    for k, v in cond.items():
        if k not in COND_KEYS:
            out.append(f"{where}: 알 수 없는 조건 키 '{k}'(쓸 수 있는 것: {', '.join(sorted(COND_KEYS))})")
        elif k == "option" and v not in kind_opts:
            out.append(f"{where}: 선택 '{v}'가 종류의 options에 없다")
        elif k == "not":
            out += _conds(v, where, kind_opts)
        elif k == "any":
            for c in v:
                out += _conds(c, where, kind_opts)
    return out


def lint(cfg):
    """구조 검사. 문제 문장 목록"""
    bad = []
    if not isinstance(cfg.get("version"), int):
        bad.append("version이 정수가 아니다")
    prot = cfg["project"]["protected"]
    for m in MUST_PROTECT:
        if m not in prot:
            bad.append(f"[project] protected에서 {m}를 뺄 수 없다")
    if not cfg["kinds"]:
        bad.append("종류가 없다")
    for kname, k in cfg["kinds"].items():
        states = k.get("states", [])
        if not states:
            bad.append(f"{kname}: 상태가 없다")
        for s in states:
            if s not in cfg["states"]:
                bad.append(f"{kname}: 상태 {s}가 [states]에 없다")
        if bad:
            continue
        opts = k.get("options", [])
        for s, cond in k.get("when", {}).items():
            if s not in states:
                bad.append(f"{kname}.when: {s}가 이 종류의 상태가 아니다")
            bad += _conds(cond, f"{kname}.when.{s}", opts)
        tpl = template_text(cfg, kname)
        ts = sections(tpl)
        uses_files = False
        for s in states:
            st = cfg["states"][s]
            for c in st.get("checks", []):
                name = c.get("check")
                if name not in CHECKS:
                    bad.append(f"{s}: 검사 '{name}'가 없다(있는 것: {', '.join(CHECKS)})")
                    continue
                if (c.get("only") and kname not in c["only"]) or kname in c.get("skip", []):
                    continue
                uses_files |= name in ("files_listed", "files_changed", "numbers", "sample")
                sec = c.get("section", DEFAULT_SECTION.get(name))
                if name == "command" and not sec:
                    continue
                for key in ("section", "options"):
                    target = c.get(key, sec if key == "section" else None)
                    if target and target not in ts:
                        bad.append(f"{kname}/{s}: {name}이 가리키는 절 '{target}'이 계획 틀에 없다"
                                   f"(상태의 sections나 종류의 extra_sections에 넣는다)")
                items = list(c.get("items", [])) + ([c["item"]] if "item" in c else [])
                if name == "sample":
                    items += ["명령", "커밋", "결과 요약"]
                have = bullets(ts.get(sec, [])) if sec else {}
                for it in items:
                    if sec in ts and it not in have:
                        bad.append(f"{kname}/{s}: {name}이 가리키는 항목 '{sec}: {it}'이 절 정의에 없다")
                if name == "reviewed" and c.get("state") not in states:
                    bad.append(f"{kname}/{s}: reviewed가 가리키는 {c.get('state')}가 이 종류에 없다")
            if st.get("review") and not isinstance(st["review"], str):
                bad.append(f"{s}: review는 문자열이다")
        if uses_files and cfg["project"]["files_section"] not in ts:
            bad.append(f"{kname}: 파일 검사를 쓰는데 files_section '{cfg['project']['files_section']}' 절이 계획 틀에 없다")
    for s in cfg["project"]["stop_states"]:
        if s not in cfg["states"]:
            bad.append(f"stop_states의 {s}가 [states]에 없다")
    for st in cfg["stages"]:
        for k in st.get("kinds", []):
            if k not in cfg["kinds"]:
                bad.append(f"{st.get('n')}단계의 종류 {k}가 [kinds]에 없다")
    return bad


# describe

def describe(cfg):
    lines = [f"구조 버전 {cfg['version']}"]
    for kname, k in cfg["kinds"].items():
        when = k.get("when", {})
        path = []
        for s in k["states"]:
            path.append(f"[{s} 조건: {fmt_cond(when[s])}]" if s in when else s)
        opts = f" (선택: {', '.join(k['options'])})" if k.get("options") else ""
        lines.append(f"- {kname}{opts}: " + " → ".join(path) + " → DONE")
    lines.append("")
    for s, st in cfg["states"].items():
        flags = [x for x in ("code", "run") if st.get(x)] + (["review"] if st.get("review") else [])
        checks = ", ".join(c["check"] for c in st.get("checks", []))
        lines.append(f"- {s}{' (' + ', '.join(flags) + ')' if flags else ''}: {checks or '검사 없음'}")
    return "\n".join(lines)


def fmt_cond(c):
    parts = []
    for k, v in c.items():
        if k == "not":
            parts.append(f"아님({fmt_cond(v)})")
        elif k == "any":
            parts.append("하나라도(" + " / ".join(fmt_cond(x) for x in v) + ")")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


# diagnose

def _durations(p):
    """상태별 머문 분. history의 연속 기록 사이 시간"""
    out = {}
    h = p.get("history", [])
    for (s, at, *_), (_, at2, *_) in zip(h, h[1:]):
        try:
            m = (datetime.fromisoformat(at2) - datetime.fromisoformat(at)).total_seconds() / 60
            out.setdefault(s, []).append(m)
        except ValueError:
            pass
    return out


def diagnose(flow):
    hz = flow.get("harness", {})
    since_ts, since_commit = hz.get("ts", 0), hz.get("commit") or ""
    lines = [f"# 하네스 진단 ({now()})", "",
             f"- 지금 구조 버전: {CFG['version']}, 마지막 adopt: {hz.get('adopted', '설치')} {hz.get('at', '')}",
             f"- PROFILE.md: {'있음' if (GATE_DIR / 'PROFILE.md').exists() else '없음'}", ""]
    stat = {}
    for pid, p in flow["plans"].items():
        cfg = cfg_for(p)
        text = (ROOT / p["plan"]).read_text(encoding="utf-8") if (ROOT / p["plan"]).exists() else ""
        ss = sections(text)
        durs = _durations(p)
        for s, at, commit, note in [tuple(x) + ("",) * (4 - len(x)) for x in p.get("history", [])]:
            key = (p["kind"], s)
            d = stat.setdefault(key, {"visits": 0, "back": 0, "skip": 0, "abandon": 0, "fail": [], "mins": [],
                                      "chars": []})
            d["visits"] += 1
            d["back"] += str(note).startswith("back")
        for s in p.get("skipped", []):
            stat.setdefault((p["kind"], s), {"visits": 0, "back": 0, "skip": 0, "abandon": 0, "fail": [], "mins": [],
                                             "chars": []})["skip"] += 1
        if p["state"] == "ABANDONED" and len(p.get("history", [])) >= 2:
            last = p["history"][-2][0]
            stat.setdefault((p["kind"], last), {"visits": 0, "back": 0, "skip": 0, "abandon": 0, "fail": [], "mins": [],
                                                "chars": []})["abandon"] += 1
        for s, rev in p.get("review", {}).items():
            if not rev.get("pass") and (p["kind"], s) in stat:
                stat[(p["kind"], s)]["fail"] += rev.get("missing", [])[:2]
        for s, ms in durs.items():
            if (p["kind"], s) in stat:
                stat[(p["kind"], s)]["mins"] += ms
        if p["state"] == "DONE":
            for s in cfg["kinds"].get(p["kind"], {}).get("states", []):
                secs = cfg["states"].get(s, {}).get("sections", [])
                n = sum(len("".join(body(ss.get(x, [])))) for x in secs)
                if secs and (p["kind"], s) in stat:
                    stat[(p["kind"], s)]["chars"].append(n)
    lines += ["## 상태별 기록", "",
              "| 종류 | 상태 | 들어온 횟수 | back으로 들어온 횟수 | 건너뜀 | 여기서 그만둠 | 머문 시간 중앙값(분) | 끝난 계획의 절 글자 수 중앙값 | 검토 FAIL 이유 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for (k, s), d in sorted(stat.items()):
        if s in CLOSED:
            continue
        med = f"{statistics.median(d['mins']):.0f}" if d["mins"] else ""
        ch = f"{statistics.median(d['chars']):.0f}" if d["chars"] else ""
        lines.append(f"| {k} | {s} | {d['visits']} | {d['back']} | {d['skip']} | {d['abandon']} | {med} | {ch} | "
                     + "; ".join(x[:60] for x in d["fail"][:3]) + " |")
    done = [p for p in flow["plans"].values() if p["state"] == "DONE" and p["entered"]["DONE"]["ts"] >= since_ts]
    lines += ["", f"- 마지막 adopt 뒤 끝난 계획: {len(done)}개"]
    ev = read_events(since_ts)
    lines += ["", "## 막힌 기록(마지막 adopt 뒤)", ""]
    if not ev:
        lines.append("없음")
    counts = {}
    for e in ev:
        counts.setdefault(e["kind"], []).append(e["reason"])
    for k, rs in sorted(counts.items(), key=lambda x: -len(x[1])):
        common = {}
        for r in rs:
            common[r[:70]] = common.get(r[:70], 0) + 1
        top = sorted(common.items(), key=lambda x: -x[1])[:3]
        lines.append(f"- {k} {len(rs)}회: " + "; ".join(f"{r} ({n}회)" for r, n in top))
    lines += ["", "## 저장소 변화(마지막 adopt 뒤)", ""]
    if since_commit:
        added = [l.split("\t")[-1] for l in git("diff", "--name-status", f"{since_commit}..HEAD").splitlines()
                 if l.startswith("A")]
        groups = {}
        for f in added:
            g = "/".join(f.split("/")[:2]) if "/" in f else f
            groups[g] = groups.get(g, 0) + 1
        lines.append("- 새로 생긴 파일(폴더별): " + (", ".join(f"{g} {n}개" for g, n in sorted(groups.items())) or "없음"))
        changed = changed_since(since_commit)
        outside = sorted(f for f in changed if not is_code(f) and not f.endswith((".md", ".json", ".lock", ".txt"))
                         and not f.startswith((".gate/", ".githooks/")))
        lines.append("- code 패턴 밖에서 바뀐 파일: " + (", ".join(outside[:20]) or "없음"))
    else:
        lines.append("- 기준 커밋이 없다")
    return "\n".join(lines)


# 제안서

def proposal_path(hid):
    return PROPOSALS / f"{hid}.md"


def proposal_config(text):
    m = re.search(r"^## 새 구조\s*\n+```toml\n(.*?)\n```", text, re.S | re.M)
    return m.group(1) if m else None


def replay(flow, new):
    """지난 계획을 새 구조에 대 본다(계획을 옮기지는 않는다). 끝난 계획은 새 구조였다면 달라졌을 상태와 절,
    진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다는 것과 새 구조와의 차이"""
    out = ["진행 중인 계획은 adopt 뒤에도 시작할 때의 구조 버전을 끝까지 쓰고, 새 구조는 다음 계획부터 적용된다. "
           "아래는 각 계획의 종류를 새 구조에 대 본 차이다.", ""]
    n0 = len(out)
    for pid, p in flow["plans"].items():
        active = p["state"] not in CLOSED
        tag = f"진행 중, 구조 v{p.get('version', CFG['version'])} 유지" if active else "끝남, 새 구조였다면"
        if p["kind"] not in new["kinds"]:
            out.append(f"- {pid} ({p['kind']}, {tag}): 새 구조에 이 종류가 없다")
            continue
        old = cfg_for(p)
        o, n = old["kinds"].get(p["kind"], {}).get("states", []), new["kinds"][p["kind"]]["states"]
        gone, added = [s for s in o if s not in n], [s for s in n if s not in o]
        old_secs = set(sections(template_text(old, p["kind"]))) if p["kind"] in old["kinds"] else set()
        new_secs = set(sections(template_text(new, p["kind"])))
        if gone or added or old_secs != new_secs:
            out.append(f"- {pid} ({p['kind']}, {p['state']}, {tag}): 빠지는 상태 {gone or '없음'}, 새 상태 {added or '없음'}, "
                       f"없어지는 절 {sorted(old_secs - new_secs) or '없음'}, 새 절 {sorted(new_secs - old_secs) or '없음'}")
    return out if len(out) > n0 else out + ["- 차이가 나는 계획 없음"]


REVIEW = ("하네스 구조 변경 제안서 {path}를 검토한다. 지금 구조 .gate/config.toml, 목적 .gate/PROFILE.md(없으면 건너뛴다), "
          "진단과 검사 결과 {diag}를 읽는다. 다음을 찾는다. (1) 상태나 검사를 빼거나 약하게 하는데 '바꾸는 것' 표의 근거가 "
          "진단 자료의 수치나 PROFILE과 맞지 않거나 없는 것 (2) PROFILE의 목적과 어긋나는 변경 (3) 근거 없이 넣은 새 상태나 "
          "경로 (4) '진행 중인 계획' 절이 재생 결과와 다른 것.")


def check_proposal(flow, hid, allow_review):
    """문제 목록. 검토 결과는 flow['proposals']에 남긴다"""
    path = proposal_path(hid)
    if not path.exists():
        return [f"{path.relative_to(ROOT).as_posix()}가 없다"]
    text = path.read_text(encoding="utf-8")
    s = sections(text)
    miss = []
    rows = [r for t in tables(s.get("바꾸는 것", [])) for r in t if r and all(r)]
    if not rows:
        miss.append("'바꾸는 것' 표에 칸이 모두 찬 행(바꾸는 것 | 근거)이 없다")
    if not body(s.get("진행 중인 계획", [])):
        miss.append("'진행 중인 계획' 절이 비었다")
    raw = proposal_config(text)
    if raw is None:
        return miss + ["'새 구조' 절에 ```toml 블록이 없다"]
    try:
        new = parse_config(raw)
    except Exception as e:
        return miss + [f"새 구조를 읽지 못했다: {e}"]
    if new["version"] != CFG["version"] + 1:
        miss.append(f"새 구조의 version은 {CFG['version'] + 1}이어야 한다(지금 {CFG['version']})")
    problems = lint(new)
    miss += [f"lint: {x}" for x in problems]
    cur = (GATE_DIR / "config.toml").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(cur.splitlines(True), (raw + "\n").splitlines(True), "지금 config.toml",
                                        "새 구조"))
    diag = PROPOSALS / f"{hid}.diag.md"
    diag.write_text("\n".join([diagnose(flow), "", "## lint", "", *(problems or ["문제 없음"]), "", "## 지난 계획 재생", "",
                               *replay(flow, new), "", "## 새 구조 경로", "", describe(new), "", "## diff", "",
                               "```diff", diff, "```", ""]), encoding="utf-8")
    if miss:
        return miss
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    rec = flow.setdefault("proposals", {}).get(hid)
    if rec and rec["hash"] == h and rec["pass"]:
        return []
    if not allow_review:
        return [f"검토자 호출 1회(약 1분)가 필요하다. check-proposal {hid} --review로 다시 실행한다"]
    result, meta = call_reviewer(REVIEW.format(path=path.relative_to(ROOT).as_posix(),
                                               diag=diag.relative_to(ROOT).as_posix()), CFG["review"].get("model"))
    flow["proposals"][hid] = {"hash": h, "pass": bool(result["pass"]), "missing": result["missing"], "at": now(), **meta}
    return [] if result["pass"] else [f"검토: {m}" for m in result["missing"]] or ["검토: FAIL"]


def adopt(flow, hid, approved):
    path = proposal_path(hid)
    text = path.read_text(encoding="utf-8")
    rec = flow.get("proposals", {}).get(hid)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    if not rec or not rec["pass"] or rec["hash"] != h:
        raise SystemExit(f"{hid}가 check-proposal 검토를 통과하지 않았거나 검토 뒤 바뀌었다. check-proposal {hid} --review를 먼저 한다")
    if rec.get("adopted"):
        raise SystemExit(f"{hid}는 이미 적용했다")
    if not approved.strip():
        raise SystemExit("--approved에 사용자가 이번 턴에 승인한 문장을 그대로 넣는다")
    raw = proposal_config(text)
    new = parse_config(raw)
    if new["version"] != CFG["version"] + 1 or lint(new):
        raise SystemExit("새 구조의 version이나 lint가 맞지 않다. check-proposal을 다시 한다")
    hist = GATE_DIR / "history"
    hist.mkdir(exist_ok=True)
    shutil.copy2(GATE_DIR / "config.toml", hist / f"v{CFG['version']}.toml")
    (GATE_DIR / "config.toml").write_text(raw.rstrip("\n") + "\n", encoding="utf-8")
    rec.update({"adopted": True, "approved": " ".join(approved.split()), "adopted_at": now()})
    flow["harness"] = {"version": new["version"], "adopted": hid, "at": now(), "ts": time.time(), "commit": head(),
                       "stage": flow.get("stage")}
    return new["version"]


def review_due(flow):
    """하네스 점검을 권할 이유 목록"""
    hz = flow.get("harness", {})
    since = hz.get("ts", 0)
    why = []
    done = sum(1 for p in flow["plans"].values() if p["state"] == "DONE" and p["entered"]["DONE"]["ts"] >= since)
    if done >= CFG["project"]["review_every_plans"]:
        why.append(f"마지막 구조 변경 뒤 계획 {done}개가 끝났다")
    ev = len(read_events(since))
    if ev >= CFG["project"]["review_every_events"]:
        why.append(f"마지막 구조 변경 뒤 hook이 {ev}번 막았다")
    if CFG["stages"] and hz.get("stage") != flow.get("stage"):
        why.append(f"프로젝트 단계가 {hz.get('stage')}에서 {flow.get('stage')}로 바뀌었다")
    if not (GATE_DIR / "PROFILE.md").exists():
        why.append("PROFILE.md(프로젝트 목적)가 없다")
    return why
