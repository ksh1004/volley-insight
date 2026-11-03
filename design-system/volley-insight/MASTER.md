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
| 모션 | 미세하게: 호버·필터 전환 150–200ms, `prefers-reduced-motion`이면 끔 | motion 3 |

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
