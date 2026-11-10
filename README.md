# dynamic-stagegate-harness

Claude Code에서 작업을 상태 기계로 진행시키는 하네스 플러그인이다. 작업 하나는 계획 파일 하나와 상태 경로를 가지고, 상태마다 할 일과 넘어가는 조건이 있다. 조건은 계획 파일, git 기록, 테스트나 eval 명령, 결과 파일로 검사하며, 상태에 맞지 않는 행동은 Claude Code hook과 git hook이 막는다. 상태 경로는 고정된 틀이 아니어서, 프로젝트가 자라면 에이전트가 진행 기록을 진단해 구조 변경을 제안하고 사용자 승인을 받아 바꾼다.

이름은 단계마다 관문을 통과해야 다음으로 넘어가는 Stage-Gate 방식에서 따왔고, 그 관문 구조가 바뀐다는 뜻으로 dynamic을 붙였다. 프로젝트 안에서는 짧게 `.flow/` 폴더와 `flow` 명령을 쓴다.

## 주요 기능

- 설치할 때 목적 인터뷰로 프로젝트의 목적, 검증 방법, 작업 종류, 실패 비용을 정해 `.flow/PROFILE.md`에 남기고, 그에 맞춰 preset 구조를 조정한다.
- `flow next`가 넘어가는 조건을 검사하고, 조건이 맞는 다음 상태로 옮긴다. 경로에는 조건을 붙일 수 있다(마이그레이션이 바뀐 계획만 MIGRATE에 들르기, `--with quick`이면 SPEC 건너뛰기).
- 판단이 필요한 상태는 읽기 도구만 쓰는 별도 Claude 호출(검토자)이 PASS를 줘야 넘어간다.
- `flow diagnose`가 상태별 기록, hook이 막은 기록, 저장소 변화를 모으고, 에이전트가 그 근거로 구조 변경 제안서를 쓴다. 제안서는 lint, 지난 계획 재생, 검토자를 거쳐 사용자가 승인한 문장이 있어야 적용된다. 진행 중인 계획은 시작할 때의 구조 버전을 끝까지 쓴다.
- 코드 상태인 계획이 없으면 코드 편집을 막고, 계획의 "변경 파일" 표에 없는 코드 파일은 커밋을 막는다. 구조 파일(config.toml, history, flowkit)은 직접 편집을 막는다.
- 이 세션이 고친 코드나 하네스 파일에 커밋하지 않은 변경이 있거나, 이번 턴에 다룬 계획이 멈추면 안 되는 상태에 있으면 턴을 끝내지 못하게 한다. 일반 질문이나 부가 작업 턴, 다른 세션의 작업으로는 막지 않고, 이번 턴에 고친 문서는 알리기만 한다.
- 진행 중인 계획을 SessionStart hook이 넣고, 메시지마다 계획 목록을 한 줄씩 넣는다. 점검할 때가 되면 SessionStart가 하네스 점검을 권한다.

## preset

| preset | 쓰는 곳 | 종류와 상태 경로 |
|---|---|---|
| ml | 대회, 연구 실험 | new와 model(RESEARCH부터 REPLAN까지 8개), tune, freeze, repro, eda, infra. 프로젝트 단계별로 허용 종류를 제한하고, 서버 실행은 커밋과 예상 종료 시각 기록 뒤에만 연다 |
| agent | LLM 에이전트, 프롬프트, 도구 | behavior(SPEC, EVALSET, IMPLEMENT, EVAL, RECORD, REPLAN), prompt, bugfix, chore. eval 케이스를 구현 전에 커밋하고 결과 파일이 새로 만들어져야 넘어간다 |
| service | 웹 서비스, 앱 | feature(SPEC, PLAN, TESTFIRST, IMPLEMENT, TEST, RECORD), bugfix(재현 명령이 REPRO에서 실패하고 FIX에서 통과), refactor, chore. TESTFIRST는 구현 전에 쓴 테스트가 실패해야 넘어가고, TEST는 그 테스트와 프로젝트 테스트 명령이 통과해야 넘어간다 |

preset은 출발점이다. 설치하면 세 preset의 구조가 `.flow/library/`에 같이 들어가서, 구조를 바꿀 때 다른 preset의 상태와 절을 빌려 쓴다.

## 폴더 구조

```
.claude-plugin/marketplace.json      로컬 마켓플레이스
plugins/dynamic-stagegate-harness/
  .claude-plugin/plugin.json
  hooks/hooks.json, run.py            설정이 있는 프로젝트에서만 그 프로젝트의 엔진을 부른다
  flowkit/                            엔진(설치하면 프로젝트의 .flow/flowkit/로 복사된다)
    core.py      설정과 구조 버전, 조건부 경로, 상태 파일, 계획 파일 파싱, git
    checks.py    넘어가는 조건 검사 함수와 검토자 호출
    evolve.py    lint, describe, diagnose, 제안서 검사, adopt
    cli.py       flow 명령과 git hook 검사
    hooks.py     guard, stop, 주입 hook
  presets/ml, agent, service/         config.toml(종류, 상태, 절 정의)
  FLOW.md                             프로젝트에 복사되는 명령과 원칙 설명
  scripts/install.py                  설치와 엔진 갱신
  skills/flow-setup, flow, flow-evolve/   설치와 목적 인터뷰, 운영, 구조 다시 짜기
```

설치한 프로젝트의 `.flow/`에는 config.toml(지금 구조), history/(앞 구조 버전), library/(빌려 쓸 구조), proposals/(구조 변경 제안서와 진단), PROFILE.md(목적), state.json(계획 상태), FLOW.md가 있다.

엔진을 프로젝트에 복사하는 이유는 프로젝트 git에 엔진 버전이 같이 남아 다시 실행했을 때 같은 규칙이 걸리게 하기 위해서다. 플러그인을 갱신한 뒤 `install.py --update`로 엔진만 바꾸고 `harness:` 커밋으로 남긴다.

## 설치

Python 3.11 이상과 git이 필요하다.

1. 마켓플레이스를 추가한다.
   ```
   claude plugin marketplace add <이 저장소 경로>
   ```
2. 플러그인을 설치한다.
   ```
   claude plugin install dynamic-stagegate-harness@dynamic-stagegate-harness
   ```
3. 하네스를 쓸 프로젝트에서 Claude Code를 열고 `/dynamic-stagegate-harness:flow-setup service`처럼 preset을 골라 부른다. 설치가 끝나면 목적 인터뷰가 이어진다.

플러그인은 사용자 범위로 설치해도 `.flow/config.toml`이 없는 프로젝트에서는 hook이 아무것도 하지 않는다.

## 구조 파일

구조는 프로젝트의 `.flow/config.toml`에 있고, 바꾸는 절차와 키 설명은 FLOW.md에 있다. 검사 함수는 `flowkit/checks.py`에 있다.

| 검사 | 통과 조건 |
|---|---|
| filled | 절이 비어 있지 않다. items를 주면 그 bullet 항목마다 값이 있다 |
| table | 절의 표에 칸이 모두 찬 행이 min_rows개 이상 있다 |
| files_listed | "변경 파일"(files_section) 절에 code 패턴에 맞는 파일이 있다 |
| files_changed | 그 파일이 이 상태에 처음 들어온 뒤 커밋에서 바뀌었다(any = true면 하나 이상) |
| clean | 코드에 커밋하지 않은 변경이 없다 |
| numbers | 코드에 새로 넣은 숫자(0과 1 제외)가 근거 표 첫 칸에 있다 |
| sample | 표본 실행 절의 커밋이 코드 파일을 마지막으로 바꾼 커밋이다 |
| results | 결과 파일이 이 상태에 들어온 뒤에 만들어졌다([remote]가 있으면 서버도 본다) |
| command | 명령이 통과한다. use = "test"면 [project] test 명령을 쓰고, expect = "fail"이면 실패해야 한다 |
| run_record | `flow run` 기록이 있다 |
| commit_since | 이 상태에 들어온 뒤 path를 바꾼 커밋이 있다(mention = true면 계획 번호를 쓴 줄) |
| choice | 고른 갈래가 결과별 다음 행동 표의 첫 칸 하나이거나 escape 문구다 |
| refs | 절에서 pattern에 맞는 참조가 file에 모두 있다 |
| paths_exist | 절에 적은 파일 경로가 모두 있다 |
| reviewed | state에서 받은 검토 뒤 계획 내용이 바뀌지 않았다 |

검사마다 `only = [종류]`나 `skip = [종류]`로 적용할 종류를 고른다.

## 한계

- 셸 명령 검사는 흔한 우회(리다이렉트, tee, sed -i, cp, mv)만 막는다. 그 밖의 방법으로 쓴 코드는 커밋 시점의 pre-commit이 잡는다. 구조 파일도 Python 스크립트로 직접 쓰면 guard가 보지 못하고, commit-msg가 `harness:` 커밋으로 따로 남기게 할 뿐이다.
- stop hook은 이 세션이 편집 도구로 고친 파일만 본다. 셸 명령이나 스크립트로 고친 파일은 pre-commit과 `clean` 검사가 잡는다.
- 숫자 검사는 .py만 tokenize로 정확히 보고, 다른 언어는 문자열과 주석을 지운 뒤 정규식으로 본다.
- 진단의 수치는 판단 자료이고, 상태를 빼거나 더할지는 에이전트의 제안과 사용자 승인으로 정한다.
