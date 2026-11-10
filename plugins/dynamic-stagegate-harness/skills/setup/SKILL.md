---
name: setup
description: 프로젝트에 stagegate 상태 기계 하네스를 설치하고, 목적을 묻는 인터뷰로 PROFILE.md와 첫 구조를 정한다. 엔진 갱신(update)도 한다. 사용자가 "stagegate 하네스 설치", "setup", "상태 기계 깔아줘", "하네스 갱신"이라고 하거나 preset(ml, agent, service)을 골라 하네스를 넣어 달라고 할 때 쓴다.
argument-hint: "<ml|agent|service|update>"
---

# stagegate 하네스 설치

인자: `$ARGUMENTS` (ml, agent, service 중 하나이거나 update)

## preset

| preset | 쓰는 곳 | 종류 |
|---|---|---|
| ml | 대회, 연구 실험. 실험 기록 log.csv, 아이디어 목록, 서버 실행 | new, model, tune, freeze, repro, eda, infra |
| agent | LLM 에이전트, 프롬프트, 도구 개발. eval을 먼저 고정하고 잰다 | behavior, prompt, bugfix, chore |
| service | 웹 서비스, 앱 개발. 테스트를 먼저 쓰고 테스트 명령으로 넘어간다 | feature, bugfix, refactor, chore |

preset은 출발점이다. 인터뷰 결과로 상태를 더하거나 빼고 경로에 조건을 붙인다. 다른 preset의 상태와 절은 `.gate/library/`에서 빌려 온다.

## 1. 설치

1. 프로젝트 루트가 git 저장소이고 커밋이 하나 이상 있는지 본다. 없으면 사용자에게 `git init`과 첫 커밋을 먼저 하자고 한다.
2. 프로젝트에 이미 다른 하네스(`.claude/settings.json`의 상태 기계 hook, `.githooks/`)가 있으면 멈추고 사용자에게 알린다. 두 하네스를 함께 돌리지 않는다.
3. 인자가 없으면 저장소를 보고 가장 가까운 preset을 권하고 사용자에게 고르게 한다.
4. 설치 스크립트를 돌린다.
   ```
   python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" <preset> --target "<프로젝트 루트>"
   ```
5. 하네스 파일만 먼저 커밋한다. `git add .gate .githooks`, `git reset .gate/state.json`, `git commit -m "harness: stagegate 하네스 설치"`. 상태 파일은 따로 커밋한다.
6. `python -X utf8 .gate/gate.py describe`로 설치된 구조를 보여 준다.

## 2. 목적 인터뷰

grilling 방식으로 묻는다. 질문은 한 번에 하나씩 하고, 질문마다 권장 답과 이유를 붙이고, 답을 듣고 다음으로 넘어간다. 저장소를 읽어 알 수 있는 사실(폴더 구조, 테스트 명령, CI 설정, 서버 설정)은 묻지 않고 찾아서 권장 답에 넣는다. 결정은 사용자에게 묻는다.

순서대로 묻되, 앞 답으로 정해진 질문은 건너뛴다.

| 주제 | 묻는 것 | 바뀌는 구조 |
|---|---|---|
| 목적 | 무엇을 만들고, 언제 성공했다고 보는가(점수, 출시, 학습) | PROFILE의 기준, REPLAN 같은 결과 판단 상태의 유무 |
| 검증 | 무엇으로 맞았는지 확인하는가(단위 테스트, e2e, eval, 로컬 점수, 사람 확인)와 그 명령 | [project] test, TESTFIRST, EVALSET, EVAL, RUN 같은 상태와 command 검사 |
| 작업 종류 | 주로 하는 일과 그 비율(기능, 버그, 실험, 프롬프트 조정, 잡일) | kinds 목록, 쓰지 않을 종류 빼기 |
| 실패 비용 | 잘못 나가면 무엇이 깨지고 되돌리기 쉬운가 | 검토자를 거는 상태, TESTFIRST를 기본으로 할지 option으로 둘지 |
| 산출물 | 코드 말고 바뀌는 것(DB 마이그레이션, 인프라 설정, 문서, 제출 파일) | 새 상태(MIGRATE 같은)와 when 조건, code 패턴 |
| 코드 위치 | 하네스가 지킬 폴더와 지키지 않을 폴더(실험 노트, 스크립트) | [project] code, tracked |
| 실행 환경 | 오래 걸리는 실행이나 원격 서버가 있는가 | [remote], run 상태 |
| 단계 | 프로젝트가 단계별로 다르게 일하는가(탐색, 개발, 동결) | [[stages]]와 단계별 허용 종류 |

인터뷰가 끝나면 정리한 답을 보여 주고 맞는지 확인받는다. 확인받기 전에는 파일을 쓰지 않는다.

## 3. PROFILE.md와 첫 구조

1. `.gate/PROFILE.md`에 답을 쓴다. 절은 목적, 성공 기준, 검증 방법, 주로 하는 작업, 실패 비용, 산출물, 코드 위치, 실행 환경, 단계다. 사용자의 말을 그대로 옮기고 해석을 덧붙이지 않는다.
2. 설치된 구조와 PROFILE이 맞지 않는 곳을 찾는다. 바꿀 곳이 없으면 PROFILE만 커밋하고 끝낸다.
3. 바꿀 곳이 있으면 evolve skill의 제안서 절차로 H001을 쓴다. 근거 칸에는 PROFILE의 해당 절을 인용한다. `check-proposal H001 --review`, 사용자 승인, `adopt` 순서로 적용한다.

## 갱신(update)

플러그인을 새 버전으로 받은 뒤 프로젝트의 엔진 복사본을 바꾼다. config.toml과 PROFILE.md는 건드리지 않는다.

```
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" --update --target "<프로젝트 루트>"
```

출력된 명령으로 하네스 커밋을 남기고, `python -X utf8 .gate/gate.py lint`가 통과하는지 본다.
