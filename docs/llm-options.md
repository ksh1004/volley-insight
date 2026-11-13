# AI 기능(ANL-12, WEB-15) LLM 선택 검토

- 작성일: 2026-11-13 / 상태: **검토만 끝남, 구현 전**. LLM 사용 여부 결정은 [요구사항 9장](requirements.md#9-미결정-사항) 10번.
- 무료 등급 조건과 모델 목록은 자주 바뀐다. 구현을 시작할 때 3·4장의 숫자를 다시 확인한다.

## 1. 결론

- **PC의 그래픽카드에서 공개 모델을 직접 실행(Ollama)하는 방식**을 1순위로 둔다. 사용량 제한이 없고, 사용자 질문이 밖으로 나가지 않으며, 라이선스를 골라 쓸 수 있다.
- 시험 후보: `qwen3.5:9b`(빠름, 질의응답용), `gpt-oss:20b`(도구 호출), `Kanana-2 30B-A3B`(한국어, 프리뷰용). 같은 시험으로 비교한 뒤 정한다(8장).
- 직접 실행 품질이 모자라면 **Groq 무료 등급**을 예비로 쓴다. LangChain에서 연결 대상을 한 줄만 바꾸면 된다.
- 어떤 모델이든 **글 속 숫자가 DB 값과 같은지 코드로 검사**한다(9장).

## 2. 두 기능이 요구하는 것

| 요구사항 | 내용 | 호출량 | 중요한 것 |
|---|---|---|---|
| ANL-12 AI 경기 프리뷰 (선택) | 승리 확률, 상대 전적, 핵심 지표로 경기 전 프리뷰 글 작성. 수치는 DB 값만, 화면에 "AI 작성" 표시 | 예측 작업(`ml_daily_predict`) 뒤 경기마다 1번 → 하루 약 10번 | 한국어 문장 품질, 숫자 정확도. 속도는 상관없음 |
| WEB-15 AI 데이터 질의응답 (선택) | "OO팀 최근 5경기 리시브 효율은?" 같은 질문에 LangChain 에이전트가 DB 조회 도구로 답하고 근거 데이터 표시 | 질문 1개 = 모델 호출 3~5번(도구 호출 왕복) | 도구 호출 정확도, 응답 속도 |

질의응답은 호출 수가 질문 수의 몇 배라서, 하루 제한이 낮은 무료 API로는 하루 몇 개 질문만 받고 끝난다.

## 3. 무료로 쓰는 방법 비교

| 방법 | 무료 한도 | 장점 | 문제 |
|---|---|---|---|
| **① PC에서 직접 실행 (Ollama)** | 제한 없음 | 데이터가 밖으로 나가지 않음, 라이선스 선택 가능 | PC가 켜져 있어야 함. 큰 API 모델보다 품질이 낮고 숫자를 지어낼 위험이 큼 |
| ② Groq 무료 등급 | 모델마다 하루 약 1,000번 (제3자 정리 기준, 정확한 값은 콘솔에서 확인) | 매우 빠름, 도구 호출 지원 | 한도가 예고 없이 바뀔 수 있음 |
| ③ Google Gemini API 무료 등급 | Flash 하루 20~250번, Flash-Lite 약 1,000번 (출처마다 다름. 구글이 공식 숫자를 더 이상 공개하지 않음) | 한국어 품질이 좋음 | **무료 등급 입력은 구글 서비스 개선에 쓰임** → 사용자 질문을 보내려면 화면에 알려야 함. 2025~26년에 여러 번 줄었음 |
| ④ OpenRouter `:free` 모델 | 하루 50번 (평생 $10 이상 충전 시 1,000번) | 한 키로 여러 모델 사용 | 무료 모델 제공사가 입력을 기록할 수 있음 |
| (비교) Claude API | 상시 무료 등급 없음 | Haiku 5.5(100만 토큰당 입력 $0.10, 출력 $0.50) 기준 프리뷰 월 약 $0.3, 질문 1,000개 월 약 $3 추정 | 선불 결제 필요 |

## 4. 직접 실행할 모델 후보

### 4.1 그래픽카드 메모리(VRAM)별 고르기

새 PC에서 먼저 `nvidia-smi`로 그래픽카드 메모리를 확인한다. Windows 화면 표시에 1~1.5GB가 이미 쓰인다.
크기는 Ollama의 기본(4비트 양자화) 파일 크기이고, 실행 중에는 대화 길이(문맥)만큼 1~2GB가 더 든다.

| 그래픽카드 메모리 | 쓸 수 있는 후보 |
|---|---|
| NVIDIA 그래픽카드 없음 | 9B 모델도 초당 몇 글자 수준으로 느림 → 직접 실행 대신 ② Groq 권장 |
| 8GB | `qwen3.5:9b`(빠듯), `qwen3.5:4b`(작은 모델이라 도구 호출이 불안정) |
| 12GB | `qwen3.5:9b`, `gemma4:12b` |
| 16GB (2026-11 개발 PC: RTX 5060 Ti) | 위 모델 + `gpt-oss:20b`. 시스템 메모리 32GB면 아래 MoE 모델도 일부를 시스템 메모리에 두고 실행 가능 |
| 24GB 이상 | `Kanana-2 30B-A3B`, `qwen3.6:35b`, `gemma4:26b`가 그래픽카드에 다 들어감. 27B급 일반 모델(`qwen3.6:27b`, `qwen3.8:27b`)도 가능 |

MoE(Mixture of Experts) 모델은 전체 크기가 커도 한 번에 일부(3~4B)만 계산한다. 그래서 그래픽카드에 다 안 들어가도 쓸 만한 속도가 나온다.
반대로 전부를 계산하는 일반(dense) 모델은 그래픽카드에서 넘치면 크게 느려진다.

### 4.2 후보 상세

| 모델 | 크기 | 구조 | 라이선스 | 도구 호출 | 한국어 근거 | 메모 |
|---|---|---|---|---|---|---|
| **Qwen 3.5 9B** `qwen3.5:9b` | 6.6~7.6GB | 일반 | Apache 2.0 (Qwen GitHub: 모든 공개 모델 Apache 2.0) | 지원, 생각 모드 | 201개 언어 지원이라고 밝힘 | 기본 후보. `qwen3.5:latest`가 이 모델 |
| **Gemma 4 12B** `gemma4:12b` | 7.7~8.0GB | 일반 | Apache 2.0 (Gemma 4부터 바뀜, 2026-04) | 지원, 생각 모드 조절 | 공개 자료 없음 | Qwen 3.5 9B와 같은 급의 대안 |
| **gpt-oss 20B** `gpt-oss:20b` | 14GB | MoE | Apache 2.0 | 지원, 추론 강도 낮음/보통/높음 | 공개 자료 없음 | 16GB 카드에서 문맥을 짧게 써야 함 |
| **Kanana-2 30B-A3B Instruct** (카카오, 2026-01) | 약 19GB (커뮤니티 GGUF, 추정) | MoE, 실제 계산 3B | **Kanana License** (5장) | 여러 단계 도구 호출 데이터로 학습, BFCL-v3 Live 74.3 | **가장 확실.** HAE-RAE 75.6 vs Qwen3-30B-A3B 57.4 (카카오 자체 평가) | Ollama 공식 목록에 없음 → Hugging Face GGUF로 받음. 기본 문맥 32K |
| Qwen 3.6 35B-A3B `qwen3.6:35b` | 23GB | MoE | Apache 2.0 | 지원 | 공개 자료 없음 | 16GB 카드 + 32GB 메모리에서는 Docker 서비스와 메모리를 다퉈 빠듯함 |
| Gemma 4 26B `gemma4:26b` | 16~19GB | MoE, 실제 계산 4B | Apache 2.0 | 지원 | 공개 자료 없음 | |

### 4.3 용도별 추천

- **질의응답(WEB-15):** `qwen3.5:9b` 또는 `gpt-oss:20b`. 그래픽카드에 다 들어가 빠르다.
- **프리뷰(ANL-12):** `Kanana-2 30B-A3B`. 한국어 근거가 가장 확실하고, 하루 한 번 일괄 작업이라 느려도 된다.
- **가능하면 한 모델로 둘 다:** Ollama는 모델을 바꿀 때마다 그래픽카드에 다시 올리므로, 시험에서 한 모델이 둘 다 해내면 그 하나만 쓴다.

## 5. 제외한 모델

| 모델 | 제외 이유 |
|---|---|
| EXAONE 3.5 / 4.0 (LG) | 비상업 라이선스. 4.0은 경쟁 모델 개발 목적의 사용도 제한 |
| HyperCLOVA X SEED Think 14B (네이버) | 상업 사용 가능, 도구 호출 지원. 다만 독자 라이선스이고 Ollama용 GGUF가 확인되지 않음 → 위 후보가 부족할 때 재검토 |
| 27B 이상 일반 모델 (`qwen3.8:27b` 18GB, `qwen3.6:27b`, `gemma4:31b`) | 16GB 카드에서 넘쳐 크게 느려짐 (24GB 이상 카드면 후보) |
| Nemotron 3.5 Lightning 30B | Ollama 페이지에 라이선스 표시가 없고 한국어 지원도 불명확 |
| 1~4B 소형 모델 (Kanana-2 1.3B/3B 등) | 도구 호출이 불안정하다는 평가 |

## 6. 라이선스 정리

- **Apache 2.0** (Qwen 3.5/3.6, Gemma 4, gpt-oss): 표시 의무가 거의 없다. 라이선스 사본과 고지만 지킨다.
- **Kanana License** (카카오 독자 라이선스, 2025-07-17판)
  - 월 사용자 1,000만 명 이하의 자체 서비스는 상업 사용도 가능하다(4.3조).
  - API나 클라우드로 모델 자체를 재판매하는 것은 별도 계약이 필요하다(4.1조).
  - **화면이나 문서에 "Powered by Kanana"를 표시**하고, 라이선스 사본과 "Kanana is licensed in accordance with the Kanana License Agreement" 고지 파일을 둬야 한다(3.1조).
  - 출력물에 대한 권리는 주장하지 않는다(5조). 단 출력으로 다른 모델을 학습시키면 파생물로 본다(1.8조).
- 모델을 정하면 `frontend/FONT_LICENSES.md`처럼 `docs/`에 모델 라이선스 기록 파일을 하나 둔다.

## 7. 다른 PC에서 시작하는 순서

1. `nvidia-smi`로 그래픽카드와 메모리를 확인하고, 4.1 표에서 후보를 고른다.
2. Ollama를 PC(Windows)에 설치하고 후보를 받는다: `ollama pull qwen3.5:9b`, `ollama pull gpt-oss:20b`. Kanana-2는 Hugging Face의 커뮤니티 GGUF로 받는다.
3. Docker 컨테이너(Airflow, Django)에서는 `http://host.docker.internal:11434`로 PC의 Ollama에 접속한다. Ollama 포트는 외부에 열지 않는다(NFR-34와 같은 원칙).
4. LangChain은 `langchain-ollama` 패키지로 연결한다. Groq·Gemini·Claude로 바꿀 때는 연결 객체만 바꾼다.
5. 8장 시험을 돌려 모델을 정한다.

## 8. 시험 계획 (약 30분)

후보 2~3개에 같은 입력을 넣고 비교한다.

- **프리뷰 1개:** 실제 예정 경기 1개의 DB 값(승리 확률, 예상 세트 스코어, 상대 전적, 팀 지표)을 JSON으로 넘겨 한국어 프리뷰를 만들게 한다.
- **질문 5개:** 도구(DB 조회 함수)를 붙여 답하게 한다. 예:
  1. 현대캐피탈 최근 5경기 리시브 효율은?
  2. 이번 시즌 여자부 공격 성공률 1위 선수는?
  3. 대한항공과 삼성화재의 최근 2년 맞대결 전적은?
  4. 다음 경기에서 이길 확률이 가장 높은 팀은?
  5. (함정) DB에 없는 기록을 묻는 질문 → "모른다"고 답하는지
- **평가 항목:** 한국어 자연스러움, 숫자가 입력과 같은지(틀린 숫자 수), 도구 호출 성공률(5개 중 몇 개), 응답 시간, 그래픽카드·시스템 메모리 사용량.

## 9. 구현할 때 지켜야 할 것

- **숫자 검사:** 프리뷰와 답변에 나온 숫자가 모델에 넘긴 값에 모두 있는지 코드로 확인한다. 없으면 다시 만들거나 글을 싣지 않는다(ANL-12 "수치는 DB 값만").
- **"AI 작성" 표시:** 프리뷰와 답변 화면에 표시한다(ANL-12). 질의응답은 근거 데이터를 함께 보여 준다(WEB-15).
- **DB 도구는 읽기 전용:** 에이전트에게 임의 SQL을 주지 않는다. 정해진 조회 함수만 도구로 주고, 그 함수도 읽기 전용 DB 계정으로 접속한다. 사용자 질문에 "테이블을 지워라" 같은 지시가 섞여 들어와도 막히게 한다.
- **요청 제한:** 질의응답 API에 로그인 API처럼 사용자별 요청 제한(throttle)을 건다. 직접 실행이면 그래픽카드를, 외부 API면 무료 한도를 지키기 위해서다.
- **외부 API를 쓰게 되면:** 사용자 질문이 외부로 전송된다는 사실을 화면에 알린다(특히 Gemini 무료 등급은 입력을 서비스 개선에 사용). 키는 `.env`에만 둔다.
- **메모리 충돌:** 시스템 메모리로 넘겨 돌리는 MoE 모델은 Docker 서비스와 메모리를 나눠 쓴다. 프리뷰 생성 시간이 Spark 작업과 겹치지 않게 한다.

## 10. 확인하지 못한 것

- 한국어 품질은 Kanana-2만 공개 점수가 있고, 그것도 카카오 자체 평가다. 나머지는 직접 시험해야 안다.
- Groq·Gemini 무료 한도는 공식 문서가 아닌 제3자 정리 글 기준이다.
- Kanana-2 GGUF 크기(약 19GB)는 30B 모델의 4비트 양자화 크기로 추정한 값이다.
- Qwen 3.5 라이선스는 Ollama 페이지에 표시가 없어 Qwen 공식 GitHub 문구로 판단했다. 받을 때 모델 카드에서 다시 확인한다.

## 출처

- Ollama: [도구 지원 모델 목록](https://ollama.com/search?c=tools), [qwen3.5](https://ollama.com/library/qwen3.5), [qwen3.6](https://ollama.com/library/qwen3.6), [qwen3.8](https://ollama.com/library/qwen3.8), [gemma4](https://ollama.com/library/gemma4), [gpt-oss](https://ollama.com/library/gpt-oss), [nemotron-3.5-lightning](https://ollama.com/library/nemotron-3.5-lightning)
- Gemma 4: [Google 발표](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/), [Apache 2.0 전환 (eWeek)](https://www.eweek.com/news/google-gemma-4-apache-2-open-models/)
- Qwen 3.6: [GGUF·라이선스 (Hugging Face)](https://huggingface.co/unsloth/Qwen3.6-27B-GGUF), [공개 정리 (noze)](https://www.noze.it/en/insights/qwen-3-6/)
- Kanana-2: [모델 카드](https://huggingface.co/kakaocorp/kanana-2-30b-a3b-instruct), [Kanana License](https://huggingface.co/kakaocorp/kanana-2-30b-a3b-instruct/blob/main/LICENSE), [카카오 발표](https://www.kakaocorp.com/page/detail/11912?lang=ENG)
- 한국 모델 라이선스: [EXAONE 등 정리 (youngju.dev)](https://www.youngju.dev/transcribe/ai/2026-08-12-huggingface-korean-models.ja), [HyperCLOVA X SEED Think 14B](https://huggingface.co/naver-hyperclovax/HyperCLOVAX-SEED-Think-14B), [HyperCLOVA X (Transformers 문서)](https://huggingface.co/docs/transformers/en/model_doc/hyperclovax)
- 16GB 그래픽카드 성능: [Ollama 성능 비교 (glukhov)](https://www.glukhov.org/ko/post/2026/01/choosing-best-llm-for-ollama-on-16gb-vram-gpu/), [16GB용 Qwen (localaimaster)](https://localaimaster.com/vram/best-qwen-model-16gb-vram)
- 무료 API: [Gemini 무료 한도 (pecollective)](https://pecollective-com.pages.dev/tools/gemini-free-tier-guide/), [Gemini 가격 변경 (agentdeals)](https://agentdeals.dev/gemini-api-pricing-changes), [Groq 무료 등급 (benchlm)](https://benchlm.ai/free-tier/groq), [무료 LLM API 비교 (novita)](https://blogs.novita.ai/free-llm-api-comparison-2026/), [OpenRouter 가격](https://openrouter.ai/pricing)
