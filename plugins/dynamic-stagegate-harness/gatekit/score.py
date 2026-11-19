"""사고 기록과 부품 성적.

막힌 기록마다 그 뒤에 같은 행동이 통과했는지를 보고 판정을 붙인다. 구조 버전과 엔진이 그대로인 채 통과했으면
작업을 고쳐서 넘어간 것이라 진짜 막힘, 구조나 엔진이 바뀐 뒤에 통과했으면 하네스를 고쳐서 넘어간 것이라 오탐,
다시 통과한 기록이 없으면 미판정이다. 성적은 부품마다 막은 횟수, 판정, 새어 나간 결함, 검토 비용을 모은다.
"""
import json

from . import parts as PT
from .core import CFG, GATE_DIR, P, cfg_for, git_path, read_events
from .records import REC, all_plans

DEFECTS = {"logic": "코드 논리", "data": "데이터 형태나 누수", "resource": "시간, 메모리, 디스크", "env": "실행 환경",
           "plan": "계획이나 판정 기준"}
CARD = GATE_DIR / "scorecard.json"
ENGINE_PART = "엔진"
KEYS = ("blocks", "true", "false", "open")
REVIEW_ROW = {"calls": 0, "spent": 0.0, "defects": 0, "formats": 0}
# 제안 기준. 근거 없이 정한 값이고 [project]에서 바꾼다
RULES = {"gate_new_plans": 2, "gate_leaks": 1, "gate_relax_false": 3, "gate_relax_window": 10, "gate_promote_true": 3}


def rule(name):
    return P.get(name, RULES[name])


def add_incident(flow, defect, note, plan="", state="", source="", quote=""):
    items = flow.setdefault("incidents", [])
    from .core import now
    import time
    rec = {"id": f"I{len(items) + 1:03d}", "at": now(), "ts": time.time(), "defect": defect, "plan": plan, "state": state,
           "source": source, "note": " ".join(note.split())[:300]}
    if quote:
        rec["quote"] = quote
    items.append(rec)
    return rec


def verdicts(events):
    """막힌 기록마다 (기록, 판정). 판정은 true, false, open"""
    out = []
    for i, e in enumerate(events):
        if e["kind"] == "next":
            same = lambda x: x["kind"] == "pass" and x.get("plan") == e.get("plan") and x.get("state") == e.get("state")
        elif e["kind"] == "guard" and e.get("key"):
            same = lambda x: x["kind"] == "guard_pass" and x.get("key") == e["key"]
        elif e["kind"] in ("guard", "stop"):
            out.append((e, "open"))
            continue
        else:
            continue
        later = next((x for x in events[i + 1:] if same(x)), None)
        if later is None:
            out.append((e, "open"))
        else:
            kept = later.get("v") == e.get("v") and later.get("engine") == e.get("engine")
            out.append((e, "true" if kept else "false"))
    return out


def tally(events, base=None):
    """부품별 막힘 집계. base는 앞에서 접어 둔 집계"""
    card = {k: dict(v) for k, v in (base or {}).items()}
    for e, verdict in verdicts(events):
        row = card.setdefault(e.get("part") or ENGINE_PART, dict.fromkeys(KEYS, 0))
        row["blocks"] += 1
        row[verdict] += 1
    return card


def folded():
    return json.loads(CARD.read_text(encoding="utf-8")).get("parts", {}) if CARD.exists() else {}


def rotate():
    """막힌 기록이 상한을 넘으면 오래된 줄을 성적 파일로 접고 지운다. 접은 줄 수"""
    f = git_path("gate-events.jsonl")
    events = read_events()
    extra = len(events) - REC["events_keep"]
    if extra <= 0:
        return 0
    old = {id(e) for e in events[:extra]}
    card = {k: dict(v) for k, v in folded().items()}
    for e, verdict in verdicts(events):  # 판정은 전체 기록으로 내고, 접을 줄만 더한다
        if id(e) in old:
            row = card.setdefault(e.get("part") or ENGINE_PART, dict.fromkeys(KEYS, 0))
            row["blocks"] += 1
            row[verdict] += 1
    CARD.write_text(json.dumps({"parts": card}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    f.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events[extra:]), encoding="utf-8", newline="\n")
    return extra


def used_parts():
    d = GATE_DIR / "parts"
    return PT.load_parts(d) if d.exists() else []


def scorecard(flow):
    """부품별 성적. 막은 횟수와 판정, covers에 해당하는 결함이 새어 나간 횟수, 검토 호출 수와 비용, 검토 지적의 결함과 형식 수"""
    card = tally(read_events(), folded())
    parts = used_parts()
    for p in parts:
        card.setdefault(p["name"], dict.fromkeys(KEYS, 0))
    for row in card.values():
        row.update(REVIEW_ROW | {"leaks": []})
    for inc in flow.get("incidents", []):
        for p in parts:
            if inc["defect"] in p.get("covers", []):
                card[p["name"]]["leaks"].append(inc["id"])
    for p in all_plans(flow).values():
        cfg = cfg_for(p)
        for state, rev in p.get("review", {}).items():
            name = cfg["states"].get(state, {}).get("review_part") or ENGINE_PART
            row = card.setdefault(name, dict.fromkeys(KEYS, 0) | REVIEW_ROW | {"leaks": []})
            row["calls"] += rev.get("calls", 1)
            row["spent"] += rev.get("spent", rev.get("cost") or 0)
            row["defects"] += rev.get("defects", 0)
            row["formats"] += rev.get("formats", 0)
    return card


def drafts(flow):
    """성적과 사고 기록에서 뽑은 제안서 초안. 판단은 에이전트와 사용자가 한다"""
    parts = used_parts()
    incidents = flow.get("incidents", [])
    out = []
    covered = {d for p in parts for d in p.get("covers", [])}
    by_defect = {}
    for inc in incidents:
        by_defect.setdefault(inc["defect"], []).append(inc)
    for d, incs in sorted(by_defect.items()):
        plans = sorted({i["plan"] for i in incs if i["plan"]})
        if d not in covered and len(plans) >= rule("gate_new_plans"):
            out.append(f"생성: 결함 분류 {d}({DEFECTS[d]})가 계획 {', '.join(plans)}에서 나왔는데 이 분류를 잡는 부품이 없다"
                       f"(사고 {', '.join(i['id'] for i in incs)}). 이 사고를 막는 검사를 project 부품으로 쓴다")
    card = scorecard(flow)
    recent = sorted((p for p in all_plans(flow).values()), key=lambda p: p["entered"]["_start"]["ts"])
    since = recent[-rule("gate_relax_window")]["entered"]["_start"]["ts"] if len(recent) >= rule("gate_relax_window") else 0
    window = tally([e for e in read_events() if e.get("ts", 0) >= since])
    for p in parts:
        row = card[p["name"]]
        if p.get("covers") and len(row["leaks"]) >= rule("gate_leaks"):
            out.append(f"강화: {p['name']}(잡으려는 분류 {', '.join(p['covers'])})를 쓰는데 그 분류의 결함이 {len(row['leaks'])}번 "
                       f"나왔다(사고 {', '.join(row['leaks'])}). 결함이 지나간 상태의 검사를 더한다")
        w = window.get(p["name"], {})
        if w.get("false", 0) >= rule("gate_relax_false") and not w.get("true"):
            out.append(f"완화: {p['name']}가 최근 계획 {rule('gate_relax_window')}개 동안 오탐 {w['false']}번, 진짜 막힘 0번이다. "
                       "검사 조건을 좁히거나 뺀다")
        if p.get("origin") == "project" and row["true"] >= rule("gate_promote_true"):
            out.append(f"올리기: project 부품 {p['name']}가 진짜 막힘 {row['true']}번을 냈다. 프로젝트 고유의 경로와 이름을 "
                       "설정으로 뺄 수 있으면 라이브러리로 올리자고 사용자에게 제안한다")
    return out


def report(flow):
    """diagnose에 넣는 사고 기록, 부품 성적, 제안 초안"""
    lines = ["## 사고 기록", ""]
    incidents = flow.get("incidents", [])
    if incidents:
        lines += ["| 사고 | 분류 | 계획 | 드러난 상태 | 출처 | 내용 |", "|---|---|---|---|---|---|"]
        lines += [f"| {i['id']} | {i['defect']} | {i['plan']} | {i['state']} | {i['source']} | {i['note'][:80].replace('|', '/')} |"
                  for i in incidents[-30:]]
    else:
        lines.append("없음")
    lines += ["", "## 부품 성적", "", "| 부품 | 막은 횟수 | 진짜 막힘 | 오탐 | 미판정 | 새어 나간 결함 | 검토 호출 | 검토 비용(USD) | 결함 지적 "
              "| 형식 지적 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in sorted(scorecard(flow).items()):
        lines.append(f"| {name} | {r['blocks']} | {r['true']} | {r['false']} | {r['open']} | "
                     f"{', '.join(r['leaks']) or 0} | {r['calls']} | {r['spent']:.2f} | {r['defects']} | {r['formats']} |")
    lines += ["", "판정: 막힌 뒤 구조 버전과 엔진이 그대로인 채 같은 행동이 통과했으면 진짜 막힘, 구조나 엔진이 바뀐 뒤에 통과했으면 "
              "오탐, 다시 통과한 기록이 없으면 미판정이다. 미판정은 사용자와 함께 본다. 결함 지적과 형식 지적은 검토자가 지적 앞에 붙인 "
              "[결함], [형식] 표시를 센다.",
              "", "## 제안 초안", "",
              "기준(근거 없이 정한 값, [project]에서 바꾼다): " + ", ".join(f"{k} = {rule(k)}" for k in RULES), ""]
    lines += [f"- {d}" for d in drafts(flow)] or ["없음"]
    return lines
