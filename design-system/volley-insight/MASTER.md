# Volley Insight 디자인 시스템 (MASTER)

모든 화면의 기준이다. 화면별 예외는 `pages/<화면>.md`에 두고, 있으면 그 규칙이 우선한다.
`ui-ux-pro-max` 스킬 검색 결과 중 이 서비스에 맞는 것만 골라 조합했다(2026-11-03).

| 항목 | 결정 | 출처 |
|---|---|---|
| 제품 유형 | 스포츠 기록·분석 (Sports Team/Club → Performance Analytics 대시보드) | `--domain product "sports scores league results"` |
| 스타일 | Data-Dense Dashboard: 표·KPI 카드·차트, 여백 최소, 격자 배치, 데이터 최대 노출 | `--design-system "sports statistics analytics dashboard" --density 8` |
| 패턴 | 랜딩 패턴은 쓰지 않는다. 검색된 Enterprise Gateway·Hero-Centric은 마케팅용이라 제외 | 검증 결과 부적합 |
| 글꼴 | Noto Sans KR (300/400/500/700). 숫자는 `font-variant-numeric: tabular-nums` | `--domain typography "korean cjk sans dashboard"` → Korean Modern |
| 밀도 | Dense: 간격 8·12·16·24·32px | density 8 |
| 모션 | 데이터가 바뀐 것을 보여줄 때만 움직인다(아래 "모션" 절). 라이브러리 없이 자체 도구(`frontend/src/motion`)로 구현, `prefers-reduced-motion`이면 최종 상태만 | motion 3 → 5 (2026-11-06) |

## 색상 토큰 (`frontend/src/App.vue` `:root`)

| 토큰 | 값 | 쓰임 |
|---|---|---|
| `--color-primary` | `#1E40AF` | 링크, 선택된 탭, 홈팀, 승리 강조 |
| `--color-accent` | `#B45309` | 원정팀, 강조(오늘 경기). 흰 배경 대비 4.5:1 이상이 되도록 `#D97706`을 한 단계 어둡게 |
| `--color-background` | `#F8FAFC` | 페이지 배경 |
| `--color-card` | `#FFFFFF` | 카드 |
| `--color-foreground` | `#0F172A` | 본문 |
| `--color-heading` | `#1E3A8A` | 제목, 핵심 숫자 |
| `--color-muted-foreground` | `#475569` | 보조 글자 (흰 배경 대비 7.5:1) |
| `--color-border` | `#E2E8F0` / 강조 `#DBEAFE` | 구분선 / 선택·호버 테두리 |
| `--color-good` / `--color-bad` | `#15803D` / `#B91C1C` | 평균 대비 좋음/나쁨. 반드시 +/− 부호와 함께 쓴다 |

팀 구분에 빨강·초록을 쓰지 않는다(좋음/나쁨과 헷갈림). 홈 = primary, 원정 = accent.

## 규칙
- **접근성:** 본문 대비 4.5:1 이상, 모든 링크·버튼·선택 상자에 `:focus-visible` 테두리, 토글 버튼은 `aria-pressed`, 차트는 `role="img"` + 요약 문장.
- **터치:** 터치 기기(`pointer: coarse`)에서 탭·선택 상자 높이 44px 이상.
- **로딩:** 빈 화면이나 "불러오는 중" 글자 대신 자리를 유지하는 스켈레톤 + `aria-busy`.
- **표:** 넓은 표는 표 안에서만 가로 스크롤(`.table-wrap`), 첫 열 고정. 정렬 가능한 표는 `aria-sort`.
- **차트:** 색만으로 구분하지 않는다(값·부호·라벨을 함께). 점수 흐름은 리드 변경 횟수·최다 연속 득점을 문장으로도 보여준다.
- **아이콘:** 이모지를 쓰지 않는다. 필요하면 SVG.
- **출처:** 모든 화면 하단에 KOVO 출처 (WEB-14).

## 모션 (2026-11-06, `frontend/src/motion`, 외부 라이브러리 없음)

| 토큰 | 값 | 쓰임 |
|---|---|---|
| `--dur-fast` | 160ms | 호버, 누름, 색 |
| `--dur-base` | 240ms | 탭 선택 표시, 작은 이동 |
| `--dur-slow` | 420ms | 행 자리 바꿈, 막대 크기, 점수 숫자 |
| `--dur-enter` | 520ms | 스크롤로 들어오는 내용 |
| `--dur-chart` | 750ms | 차트가 그려지며 등장 |
| `--ease-out` / `--ease-in-out` / `--ease-spring` | 감속 / 두 곳 사이 이동 / 작은 튀어오름(점·아이콘만) | |

도구: `useTween`(숫자가 새 값으로 미끄러짐), `useInView`, `useFlash`, `AnimatedNumber`, `RollingNumber`(자릿수가 굴러가는 점수),
`v-reveal`(스크롤 등장, 형제끼리 45ms 차이), `v-glider`(탭 선택 표시가 미끄러짐), 페이지 전환(`page`), 목록 이동(`row`, `item`), `swap`, `.lift`.

규칙:
- 움직임은 데이터나 위치가 바뀐 것을 알려줄 때만 쓴다. 장식용 반복 애니메이션은 없다(로딩 스켈레톤과 실시간 점만 반복).
- transform·opacity만 움직인다. 너비·높이를 애니메이션하지 않는다(막대는 `scaleX`).
- 첫 화면 자료(실시간 첫 스냅숏 등)는 그냥 나타나고, 그 뒤의 변화만 움직인다.
- 숫자가 움직여도 화면 읽기 프로그램에는 최종 값 하나만 읽힌다(움직이는 숫자는 `aria-hidden`).
- 차트 상호작용은 마우스·키보드 둘 다: 좌우 화살표로 값, Elo 차트는 끌어서 확대 + `+`/`-`/`0` 키와 버튼, 요약 항목에 올리면 차트에 표시.
- `prefers-reduced-motion: reduce`이면 모든 움직임 없이 최종 상태를 보여준다(내용이 숨겨진 채 남지 않는다).
- 다시 불러오는 동안 이전 값은 흐리게(`.busy`) 남기고, 새 값이 오면 그 자리에서 미끄러지게 바꾼다(순위표, 예측 검증, 일정).
- 기준선이 있는 막대(리그 평균 비교, 선수 유형)는 기준선에서 양쪽으로 자란다. 여러 줄이면 줄마다 50~70ms씩 늦게.
- 차트·막대의 항목에 올리면(또는 포커스하면) 문장으로 풀어 준다(세트 스코어 확률, 레이더 축, 점수 흐름 요약). 터치 기기에서는 안내 문구를 숨긴다.

