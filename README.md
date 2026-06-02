# 치지직 타임라인 매니저 

# (Chzzk-Timeline-Manager)

치지직(CHZZK) 다시보기(VOD)의 음성 데이터와 실시간 채팅 화력을 분석하여, Gemini AI 기반 하이라이트 타임라인을 자동 생성하고 댓글까지 등록해주는 자동화 도구입니다.

---

# [⚙️ 주요 설정으로 이동](#️-configjson-설정)

# ✨ 프로젝트 개요

이 프로젝트는 방송 전체 흐름 속에서:

* 스트리머 발언
* 시청자 반응
* 채팅 화력 집중 구간
* 주요 방송 흐름

을 동시에 분석하여 타임라인을 생성합니다.

핵심 기능:

* Faster-Whisper 기반 음성 전사(STT)
* 채팅 화력 분석 및 노이즈 제거
* Gemini AI 구조화 요약
* 스트리머 발언 기준 싱크 보정
* 치지직 댓글 자동 등록

---

# 🚀 주요 기능

## 🎵 VOD 오디오 및 채팅 수집

* yt-dlp 기반 고속 오디오 다운로드
* ffmpeg 자동 MP3 변환
* 다운로드 캐싱 지원
* 치지직 채팅 전체 로그 저장
* 특정 구간(%)만 선택 분석 가능

---

## 🧠 Faster-Whisper 기반 STT

* CUDA GPU 자동 사용
* CPU fallback 지원
* 긴 방송 자동 슬라이싱 처리
* 한국어 고정밀 음성 전사

---

## 🔥 채팅 화력 분석

다음 요소를 기반으로 하이라이트를 탐지합니다.

* 채팅 밀도
* 폭발 반응 구간
* 반복 반응 빈도
* 감정 반응 집중도

자동 정제 대상:

* 과도한 ㅋㅋㅋ / ㅎㅎㅎ
* 반복 도배
* 특수문자 이모티콘
* 무의미한 스팸 텍스트

---

## 🤖 Gemini AI 기반 타임라인 생성

Gemini AI가 다음 구조로 방송 내용을 정리합니다.

```text
[대주제; 소주제]
[타임스탬프] - 내용
```

예시:

```text
[저스트 채팅; 방송 시작]
[00:03:05] 인사 및 방송 컨디션 이야기

[게임; 랭크 시작]
[01:12:33] 경쟁전 큐 시작 및 팀원 반응
```

---

## 💬 댓글 자동 등록

config.json에 입력된 `NID_AUT`, `NID_SES`로 타임라인 생성 후 치지직 댓글로 바로 등록할 수 있습니다.

---

# 📄 파일 설명

## Main.py

CLI 인터페이스 및 전체 실행 흐름 담당

* 모드 선택
* 사용자 입력 처리
* 댓글 등록 제어

---

## Timeline.py

핵심 AI 처리 모듈

* 오디오 다운로드
* Whisper 전사
* 채팅 정제
* 화력 분석
* Gemini 분석
* 타임라인 생성

---

## Chzzk_api.py

치지직/네이버 API 처리

* VOD 조회
* 채팅 수집
* 브라우저 쿠키 탐색
* 댓글 등록 API 호출

---

## config.json

* 환경 설정 파일

---

# ⚙️ config.json 설정

## 예시

```json
{
    "TARGET_CHANNEL_ID": "치지직_32자리_채널_해시값",
    "GEMINI_API_KEY": "본인의_GEMINI_API_KEY",
    "NID_AUT": "네이버 쿠키에서 추출한 고유 인증 토큰 1",
    "NID_SES": "네이버 쿠키에서 추출한 세션 인증 토큰 2",
    "WHISPER_LANGUAGE": "ko",
    "WHISPER_MODEL": "base"
}

```

### 쿠키 추출 방법

1. 네이버 또는 치지직 로그인
2. F12 → Application → Cookies
3. `https://chzzk.naver.com`
4. `NID_AUT`, `NID_SES` 값 복사

---

# 🧠 WHISPER_MODEL 설명

| 모델       | 속도    | 정확도   | 특징       |
| -------- | ----- | ----- | -------- |
| tiny     | 매우 빠름 | 낮음    | 저사양용     |
| base     | 빠름    | 보통    | 기본 추천    |
| small    | 빠름    | 준수    | 밸런스형     |
| medium   | 보통    | 좋음    | 방송 분석 추천 |
| large-v3 | 느림    | 매우 높음 | 최고 정확도   |
| turbo    | 매우 빠름 | 높음    | 최신 고성능   |

추천:

* 빠른 테스트: `base`, `small`
* 고품질 분석: `medium`, `turbo`

---

# 🛠️ Prompt 커스텀

프로젝트 루트에 아래 파일을 추가하면 AI 분석 품질을 개선할 수 있습니다.

## prompt.txt

AI 행동 지침

예시:

```text
- 반복 표현 최소화
- 게임명 정확히 표기
- 핵심 장면 위주 정리
```

---

## streamer_info.txt

스트리머 정보 및 밈 보정

예시:

```text
[방송 정보]
- 스트리머: 담유이
- 팬덤: 아담이
- 주요 콘텐츠: 저스트 채팅, FPS 게임
```

활용 목적:

* STT 오타 보정
* 밈 인식
* 합방 멤버 구분
* 고유명사 정확도 향상

---

# 🛠️ 설치 방법

## .bat에 있는 Chzzk-Timeline-Manager.zip 설치하셔도 됩니다.

## Python 설치

* Python 3.10 이상 권장

---

## FFmpeg 설치

```bash
ffmpeg -version
```

PATH 등록 필요

---

## 패키지 설치

```bash
pip install -r requirements.txt
```

# 🔥 CUDA 환경에서 PyTorch 재설치 (GPU 가속 필수)

Faster-Whisper 및 AI 연산 속도를 제대로 활용하려면 CUDA가 활성화된 PyTorch 환경을 사용하는 것을 권장합니다.

먼저 기존 torch를 제거합니다.

```bash id="v8m31m"
pip uninstall torch torchvision torchaudio -y
```

---

## CUDA 12.1 환경 (권장)

최신 NVIDIA 드라이버 사용 시 추천

```bash id="p9m8qb"
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

---

## CUDA 11.8 환경

구형 환경 또는 호환성 우선 시 사용

```bash id="fsc69q"
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

---

# 📚 주요 의존성

```text
faster-whisper
yt-dlp
ffmpeg-python
google-generativeai
pydantic
browser-cookie3
torch
ctranslate2
```

---

# ▶️ 실행 방법

```bash
python Main.py
```

---

# ⚡ 성능 참고

| 환경      | 처리 속도 |
| ------- | ----- |
| RTX GPU | 매우 빠름 |
| CPU     | 느림    |

긴 방송도:

* 청크 분할
* 캐싱
* 부분 분석

구조로 안정 처리합니다.

---

# ⚠️ 주의사항

* 치지직 댓글 제한: 5000자
* 비공식 API 기반 프로젝트
* 치지직 구조 변경 시 일부 기능이 동작하지 않을 수 있음

---

# 📜 라이선스 및 면책사항

본 프로젝트는 개인 편의를 위한 비공식 자동화 도구입니다.

NAVER 및 CHZZK와 제휴 관계가 없으며, 댓글 내용·저작권·플랫폼 정책 위반 등에 대한 책임은 사용자 본인에게 있습니다.

---


