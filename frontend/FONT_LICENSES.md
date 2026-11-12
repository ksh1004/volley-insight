# 글꼴 라이선스

이 사이트가 쓰는 글꼴과 라이선스(2026-11-11, 각 글꼴의 공식 배포처에서 라이선스 원문 확인).
모두 **SIL Open Font License 1.1(OFL)** 이다. 글꼴을 새로 넣을 때는 이 표에 추가하고, OFL·Apache-2.0처럼 자유 라이선스인 것만 쓴다.

| 글꼴 | 쓰는 곳 (`frontend/src/tokens.css`) | 저작권자 | 라이선스 원문 | 불러오는 곳 |
|---|---|---|---|---|
| Pretendard Variable | 본문 전체 (`--font-body`) | Kil Hyung-jin. Source Han Sans(Adobe), Inter, M PLUS 1을 바탕으로 만듦 | https://github.com/orioncactus/pretendard/blob/main/LICENSE | jsDelivr CDN |
| Saira Condensed (800) | 제목·팀 이름·스코어의 영문·숫자 (`--font-display`) | The Saira Project Authors (Omnibus-Type) | https://github.com/google/fonts/blob/main/ofl/sairacondensed/OFL.txt | Google Fonts |
| Black Han Sans (검은고딕) | 제목·팀 이름의 한글 (`--font-display`) | The Black Han Sans Project Authors (Zess Type) | https://github.com/google/fonts/blob/main/ofl/blackhansans/OFL.txt | Google Fonts |
| JetBrains Mono (500, 700) | 시각·기록 숫자 (`--font-outlier`) | The JetBrains Mono Project Authors | https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt | Google Fonts |

## OFL 1.1에서 지킬 것

- **해도 되는 것:** 웹사이트에 쓰기, 상업적 이용, 화면·이미지·인쇄물에 쓰기, 글꼴 파일을 다른 소프트웨어와 함께 배포하기.
- **하면 안 되는 것:** 글꼴 파일 자체를 단독으로 파는 것. 글꼴을 고쳐서 배포할 때 원래 이름(예약 글꼴 이름: Pretendard, Saira, Source, Inter, M PLUS 1 등)을 그대로 쓰는 것.
- **글꼴 파일을 저장소에 넣어 직접 배포한다면(셀프 호스팅):** 파일과 함께 위 라이선스 원문(OFL.txt)을 같이 넣는다.
  지금은 CDN에서 불러오기만 하므로 이 저장소가 글꼴 파일을 배포하지 않는다.

## 대체 글꼴

`tokens.css`의 글꼴 목록 끝에 있는 `Noto Sans KR`(OFL), `Apple SD Gothic Neo`, `Malgun Gothic`, `system-ui`, `sans-serif`는
위 글꼴을 불러오지 못했을 때 **방문자 컴퓨터에 이미 설치된 글꼴**을 이름으로 가리키는 것뿐이다.
이 사이트가 그 글꼴 파일을 배포하거나 포함하지 않으므로 라이선스 문제가 생기지 않는다.
