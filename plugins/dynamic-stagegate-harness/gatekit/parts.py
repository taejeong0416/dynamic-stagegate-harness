"""부품을 조립해 구조(config.toml)를 만든다.

부품은 <이름>/part.toml이다. 경로 부품은 종류([kinds])와 그 상태를 정하고 상태마다 roles를 붙인다. 다른 부품은
[[attach]]로 역할이나 이름이 맞는 상태에 절, 할 일, 검사를 더하고 [[insert]]로 역할 앞뒤에 상태를 넣는다.
같은 부품 목록과 같은 덮어쓰기(local.toml)면 같은 구조가 나온다.
"""
import copy
import hashlib
import json
import re
import tomllib
from pathlib import Path

# 조립 결과에 넣지 않는 부품 키
META = ("name", "description", "version", "origin", "source", "provenance", "applies_when", "requires", "conflicts", "covers",
        "attach", "insert")
TABLES = ("kinds", "sections", "states")
SETTINGS = ("project", "remote", "review", "records")
# attach에서 대상을 고르는 키와 순서 키. 나머지는 상태에 더하는 내용이다
ATTACH_KEYS = ("roles", "states", "only", "skip", "at")


def load_part(folder):
    folder = Path(folder)
    part = tomllib.loads((folder / "part.toml").read_text(encoding="utf-8"))
    part.setdefault("name", folder.name)
    part["_hash"] = part_hash(folder)
    return part


def load_parts(folder):
    return [load_part(f.parent) for f in sorted(Path(folder).glob("*/part.toml"))]


def part_hash(folder):
    h = hashlib.sha256()
    for f in sorted(Path(folder).iterdir()):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def rank(part):
    """쌓는 순서. 경로 부품, library 부품, project 부품"""
    return 0 if part.get("kinds") else 1 if part.get("origin", "library") == "library" else 2


def lock(parts):
    """조립에 쓴 부품의 버전, 출처, 파일 해시"""
    return {p["name"]: {k: p[k] for k in ("version", "origin", "source") if k in p} | {"hash": p.get("_hash", "")}
            for p in sorted(parts, key=lambda p: p["name"])}


def lock_hash(parts):
    return hashlib.sha256(json.dumps(lock(parts), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _kinds_using(cfg, state):
    return [k for k, v in cfg["kinds"].items() if state in v.get("states", [])]


def _passes(rule, kind):
    return (not rule.get("only") or kind in rule["only"]) and kind not in rule.get("skip", [])


def _targets(cfg, rule):
    """attach가 붙을 상태. 역할이나 이름이 맞고, 그 상태를 쓰는 종류 가운데 only와 skip을 통과하는 것이 있어야 한다"""
    out = []
    for name, st in cfg["states"].items():
        if name not in rule.get("states", []) and not set(st.get("roles", [])) & set(rule.get("roles", [])):
            continue
        users = _kinds_using(cfg, name)
        if not users or any(_passes(rule, k) for k in users):
            out.append(name)
    return out


def assemble(parts, local=None):
    """(구조, 문제 목록). 구조는 config.toml과 같은 모양의 dict다"""
    bad = []
    parts = sorted(parts, key=lambda p: (rank(p), p["name"]))
    names = [p["name"] for p in parts]
    bad += [f"부품 {n}이 두 번 들어 있다" for n in sorted({n for n in names if names.count(n) > 1})]
    for p in parts:
        bad += [f"{p['name']}: 필요한 부품 {r}이 없다" for r in p.get("requires", []) if r not in names]
        bad += [f"{p['name']}: 부품 {c}와 같이 둘 수 없다" for c in p.get("conflicts", []) if c in names]
    cfg = {"version": 1, "project": {}, "remote": {}, "review": {}, "records": {}, "stages": [], "kinds": {},
           "sections": {}, "states": {}, "guard": []}
    owner = {}

    def setting(table, key, val, who):
        cur = cfg[table].get(key)
        if isinstance(val, list):
            cfg[table][key] = (cur or []) + [x for x in val if x not in (cur or [])]
        elif cur is not None and cur != val:
            bad.append(f"[{table}] {key}: {owner[table, key]}와 {who}가 다르게 정한다({cur!r}, {val!r})")
        else:
            cfg[table][key] = val
        owner.setdefault((table, key), who)

    for p in parts:
        for table in SETTINGS:
            for key, val in p.get(table, {}).items():
                setting(table, key, val, p["name"])
        cfg["guard"] += [dict(g, part=p["name"]) for g in p.get("guard", [])]
        if p.get("stages"):
            if cfg["stages"]:
                bad.append(f"[[stages]]: {owner['stages']}와 {p['name']}가 함께 정한다")
            else:
                cfg["stages"], owner["stages"] = copy.deepcopy(p["stages"]), p["name"]
        for table in TABLES:
            for key, val in p.get(table, {}).items():
                if key not in cfg[table]:
                    cfg[table][key], owner[table, key] = copy.deepcopy(val), p["name"]
                elif cfg[table][key] != val:
                    bad.append(f"[{table}.{key}]: {owner[table, key]}와 {p['name']}가 다르게 정의한다")

    # 상태 넣기. 종류마다 역할이 맞는 상태의 앞이나 뒤에 넣고, 뒤에 넣은 상태는 앞 상태의 back을 물려받는다
    for p in parts:
        for ins in p.get("insert", []):
            state, role = ins.get("state"), ins.get("before") or ins.get("after")
            if state not in cfg["states"]:
                bad.append(f"{p['name']}: insert가 넣는 상태 {state}가 정의되지 않았다")
                continue
            placed = False
            for kname, k in cfg["kinds"].items():
                path = k.get("states", [])
                hits = [s for s in path if role in cfg["states"].get(s, {}).get("roles", [])]
                if not _passes(ins, kname) or not hits or state in path:
                    continue
                anchor = hits[0] if ins.get("before") else hits[-1]
                path.insert(path.index(anchor) + (0 if ins.get("before") else 1), state)
                if ins.get("after") and anchor in k.get("back", {}):
                    k["back"][state] = list(k["back"][anchor])
                placed = True
            if not placed:
                bad.append(f"{p['name']}: {state}를 넣을 자리(역할 {role})가 어느 종류에도 없다")

    # 상태에 더하기. 상태 정의를 at 50으로 두고 at, 쌓는 순서, 부품 이름 순으로 잇는다
    def marked(fields, who):
        """검사와 검토에 어느 부품의 것인지 적는다. 막힌 기록과 검토 비용을 부품별로 모으는 데 쓴다"""
        out = copy.deepcopy({k: v for k, v in fields.items() if k not in ATTACH_KEYS and k != "roles"})
        out["checks"] = [dict(c, part=who) for c in out.get("checks", [])]
        if not out["checks"]:
            del out["checks"]
        if out.get("review"):
            out["review_part"] = who
        return out

    blocks = {name: [(50, -1, "", 0, marked(st, owner["states", name]))] for name, st in cfg["states"].items()}
    for p in parts:
        for i, rule in enumerate(p.get("attach", [])):
            targets = _targets(cfg, rule)
            if not targets:
                bad.append(f"{p['name']}: attach {i + 1}번이 붙을 상태가 없다"
                           f"(역할 {rule.get('roles', [])}, 상태 {rule.get('states', [])})")
            for name in targets:
                blocks[name].append((rule.get("at", 50), rank(p), p["name"], i, marked(rule, p["name"])))
    for name, items in blocks.items():
        roles, st, src = cfg["states"][name].get("roles"), {}, {}
        for _, _, who, _, fields in sorted(items, key=lambda b: b[:4]):
            for key, val in fields.items():
                if isinstance(val, list):
                    st[key] = st.get(key, []) + [x for x in copy.deepcopy(val) if key == "checks" or x not in st.get(key, [])]
                elif key in st and st[key] != val and key != "review_part":
                    bad.append(f"[states.{name}] {key}: {src.get(key) or owner['states', name]}와 {who}가 다르게 정한다")
                else:
                    st[key], src[key] = val, who
        cfg["states"][name] = st | ({"roles": roles} if roles else {})

    for table, vals in (local or {}).items():
        if table in SETTINGS:
            cfg[table].update(vals)
        elif table == "stages":
            cfg["stages"] = copy.deepcopy(vals)
        elif table in TABLES:
            for key, val in vals.items():
                cfg[table].setdefault(key, {}).update(copy.deepcopy(val))
        elif table == "version":
            cfg["version"] = vals
        else:
            bad.append(f"local.toml: 알 수 없는 표 [{table}]")
    return _ordered(cfg), bad


def _ordered(cfg):
    """상태는 종류의 경로에 나오는 순서, 절은 상태가 쓰는 순서로 놓고 빈 표는 뺀다"""
    states = []
    for k in cfg["kinds"].values():
        states += [s for s in k.get("states", []) if s not in states and s in cfg["states"]]
    states += [s for s in cfg["states"] if s not in states]
    secs = []
    for s in states:
        secs += [x for x in cfg["states"][s].get("sections", []) if x not in secs and x in cfg["sections"]]
    for k in cfg["kinds"].values():
        secs += [x for x in k.get("extra_sections", []) if x not in secs and x in cfg["sections"]]
    secs += [x for x in cfg["sections"] if x not in secs]
    out = dict(cfg, states={s: cfg["states"][s] for s in states}, sections={x: cfg["sections"][x] for x in secs})
    return {k: v for k, v in out.items() if v or k in ("version", "project", "kinds", "states")}


# TOML 쓰기. config.toml에 나오는 모양(표, 표 배열, 한 줄 표, 문자열, 정수, 참거짓, 목록)만 다룬다

def _key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    raise TypeError(f"TOML로 쓸 수 없는 값: {v!r}")


def _line(k, v):
    if isinstance(v, str) and "\n" in v and '"""' not in v and "\\" not in v:
        return f'{_key(k)} = """\n{v}"""'
    if isinstance(v, list) and v and (any(isinstance(x, dict) for x in v) or len(_val(v)) > 100):
        return f"{_key(k)} = [\n" + "".join(f"  {_val(x)},\n" for x in v) + "]"
    return f"{_key(k)} = {_val(v)}"


def dumps(cfg, head=""):
    """config.toml이나 part.toml 글. 밑줄로 시작하는 키는 쓰지 않는다"""
    arrays = ("stages", "guard", "insert", "attach")
    out = [head.rstrip("\n")] if head else []
    out += [_line(k, v) for k, v in cfg.items() if k not in (*SETTINGS, *TABLES, *arrays) and not k.startswith("_")]
    for table in SETTINGS:
        if cfg.get(table):
            out += ["", f"[{table}]"] + [_line(k, v) for k, v in cfg[table].items()]
    for st in cfg.get("stages", []):
        out += ["", "[[stages]]"] + [_line(k, v) for k, v in st.items()]
    for table in TABLES:
        for name, body in cfg.get(table, {}).items():
            out += ["", f"[{table}.{_key(name)}]"] + [_line(k, v) for k, v in body.items()]
    for table in arrays[1:]:
        for item in cfg.get(table, []):
            out += ["", f"[[{table}]]"] + [_line(k, v) for k, v in item.items()]
    return "\n".join(out) + "\n"


# 적용 조건. 검사가 판정할 수 있는 사실로만 쓴다

def holds(cond, root, answers):
    """부품의 applies_when이 맞는지. 키를 모두 만족해야 한다.
    file: 저장소에 glob에 맞는 파일이 있다, command: 명령이 성공한다, answer: 고정 질문에 이 답을 했다(답이 여러 개면
    그중 하나), not: 아래 조건의 부정, any: 아래 조건 중 하나, all: 아래 조건 모두"""
    for key, v in cond.items():
        if key == "file":
            ok = any(next(Path(root).glob(g), None) for g in ([v] if isinstance(v, str) else v))
        elif key == "command":
            import subprocess
            try:
                ok = subprocess.run(v, shell=True, cwd=root, capture_output=True, timeout=60).returncode == 0
            except subprocess.TimeoutExpired:
                ok = False
        elif key == "answer":
            ok = all(want in (got if isinstance(got := answers.get(q, []), list) else [got]) for q, want in v.items())
        elif key == "not":
            ok = not holds(v, root, answers)
        elif key in ("any", "all"):
            ok = (any if key == "any" else all)(holds(c, root, answers) for c in v)
        else:
            raise SystemExit(f"applies_when: 알 수 없는 조건 키 '{key}'")
        if not ok:
            return False
    return True


def fmt_cond(cond):
    out = []
    for key, v in cond.items():
        if key == "answer":
            out += [f"답 {q} = {want}" for q, want in v.items()]
        elif key == "not":
            out.append(f"아님({fmt_cond(v)})")
        elif key in ("any", "all"):
            out.append("(" + (" 또는 " if key == "any" else " 그리고 ").join(fmt_cond(c) for c in v) + ")")
        else:
            out.append(f"{key} {v}")
    return ", ".join(out)


def select(parts, root, answers):
    """(조건이 맞은 부품, 맞지 않은 부품). 조건이 없는 부품은 고르지 않는다"""
    on = [p for p in parts if p.get("applies_when") and holds(p["applies_when"], root, answers)]
    return on, [p for p in parts if p not in on]


def write_lock(gate_dir, parts, old=None):
    """parts.lock을 쓴다. from은 라이브러리에서 복사할 때의 해시다. 부품 파일이 from과 같으면 프로젝트에서 고치지 않은 것이다"""
    lib = {f.parent.name: part_hash(f.parent) for f in (Path(gate_dir) / "library").glob("*/part.toml")}
    out = lock(parts)
    for name, rec in out.items():
        if lib.get(name) == rec["hash"]:
            rec["from"] = rec["hash"]
        elif (old or {}).get(name, {}).get("from"):
            rec["from"] = old[name]["from"]
    (Path(gate_dir) / "parts.lock").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def read_lock(gate_dir):
    f = Path(gate_dir) / "parts.lock"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def read_local(gate_dir):
    """(덮어쓰기, 고정 질문의 답). local.toml의 [answers]는 부품을 고르는 데만 쓴다"""
    f = Path(gate_dir) / "local.toml"
    local = tomllib.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return {k: v for k, v in local.items() if k != "answers"}, local.get("answers", {})


HEAD = "# 부품(.gate/parts)과 덮어쓰기(.gate/local.toml)를 조립한 구조다. 제안서와 gate adopt로만 바뀐다(.gate/GATE.md)."


def build(gate_dir, version=1):
    """gate_dir의 부품과 덮어쓰기로 조립한다. (구조, 문제 목록, 부품 목록)"""
    used = load_parts(Path(gate_dir) / "parts")
    cfg, bad = assemble(used, read_local(gate_dir)[0])
    cfg["version"] = version
    return cfg, bad, used
