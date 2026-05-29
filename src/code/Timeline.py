import os
import sys
import json
import warnings
import subprocess
import re
import time
from datetime import datetime, timedelta
from sympy import content
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, Field
from typing import List, Optional

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"
GEMINI_MODEL = "gemini-3.1-flash-lite"      
TEMPERATURE = 0.2  
MAX_OUTPUT_TOKENS = 4000             
TOP_P = 0.95                          

class TimelineItem(BaseModel):
    group_large: str = Field(
        description="방송 상황의 대분류이자 대주제 (예: 저스트 채팅, 게임 방송, 공지사항, 영도 시청 등)"
    )
    topic: str = Field(
        description=(
            "현재 시간대의 실제 구체적인 대화 주제나 진행 중인 콘텐츠/게임 이름 등의 소주제.\n"
            "🚨 [합방 멤버 표기 고정 규칙]: 만약 다른 스트리머와 합방 중이거나, "
            "디스코드/음성 채널로 대화 및 소통 중인 상황이라면 소주제 이름 뒤에 반드시 괄호를 열고 대화 중인 멤버들의 이름을 명시하십시오.\n"
            "예시 1: '배틀그라운드(합방 멤버)'\n"
            "예시 2: '디스코드 잡담(합방 멤버)'\n"
            "합방이나 대화 중이 아니라면 평소처럼 콘텐츠 이름만 깔끔하게 적으십시오."
        )
    )
    timestamp: str = Field(description="[HH:MM:SS] 형식의 시간 축 지점")
    wf: int = Field(description="재미 점수 (0 ~ 50)")
    wi: int = Field(description="중요 점수 (0 ~ 50)")
    content: str = Field(
        description=(
            "🚨 [시간 마이크로 매칭 및 스트리머 멘트 최우선 매칭 제약]:\n"
            "1. 만약 특정 리액션이나 내용에 대해 시청자의 채팅 반응과 스트리머의 오디오 발언이 거의 동시에 일어났다면, "
            "무조건 스트리머가 직접 말한 최초 발언 시점의 텍스트와 시간만을 기준으로 content를 작성하십시오.\n"
            "2. 문장은 10~15자 내외로 극도로 짧고 간결해야 합니다. 구구절절한 설명 조나 나열식 문장은 절대 금지입니다.\n"
            "3. 문장 끝은 깔끔한 명사 형태('~모습', '~이야기', '~리액션', '~인사')로 자연스럽게 끝맺음 하십시오.\n"
            "4. 🚨 '모바', '포바' 같은 단어는 모바일 게임이나 포토가 아니라 닉네임 축약형 '작별/퇴근 인사말'입니다. 문맥을 파악하여 '인사 소통'이나 '방종 인사' 등으로 변환하여 출력하십시오.\n"
            "5. 본문 내용 안에 단락 태그를 중복해서 절대 삽입하지 마십시오."
        )
    )

class TimelineResponse(BaseModel):
    items: List[TimelineItem] = Field(description="추출된 방송 타임라인 조각 리스트")


def timestamp_to_seconds(ts_str: str) -> int:
    ts_str = ts_str.strip().strip("[]")
    parts = ts_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def seconds_to_timestamp(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"[{h:02d}:{m:02d}:{s:02d}]"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "TARGET_CHANNEL_ID": "채널_ID_입력",
            "CHZZK_CLIENT_ID": "YOUR_CHZZK_CLIENT_ID",
            "CHZZK_CLIENT_SECRET": "YOUR_CHZZK_CLIENT_SECRET",
            "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
            "WHISPER_MODEL": "base" 
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print(f"\n⚙️  [안내] 프로젝트 폴더에 '{CONFIG_FILE}' 파일이 생성되었습니다.")
        sys.exit(0)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        return (
            config.get("TARGET_CHANNEL_ID", "").strip(), 
            config.get("GEMINI_API_KEY", "").strip(),
            config.get("WHISPER_MODEL", "base").strip() 
        )
    except Exception as e:
        print(f"❌ [JSON 파싱 실패] config.json 파일을 읽는 중 오류 발생: {e}")
        sys.exit(1)

def get_video_duration(chzzk_url):
    ydl_opts = {'quiet': True, 'nocheckcertificate': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(chzzk_url, download=False)
        return info.get('duration', 0)


def download_chzzk_vod_audio(chzzk_url, vod_id, output_filename="full_vod_audio"):
    specific_palette_dir = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}")
    os.makedirs(specific_palette_dir, exist_ok=True)
    
    master_audio_mp3 = os.path.join(specific_palette_dir, f"{output_filename}.mp3")
    raw_master_tmpl = os.path.join(specific_palette_dir, "raw_master_stream")
    
    if os.path.exists(master_audio_mp3) and os.path.getsize(master_audio_mp3) > 10240:
        print(f"✨ [오디오 캐시 적중] 전체 원본 오디오 로드 완료: {master_audio_mp3}")
        return master_audio_mp3

    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        print("❌ VOD 메타데이터 파싱 실패.")
        return ""

    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg_bin = "ffmpeg"

    print(f"\n📡 [최초 1회 실행] 16개 스레드 비동기 전체 오디오 수집 개시...")
    
    ydl_opts = {
        'format': 'worstaudio/worst',
        'outtmpl': f'{raw_master_tmpl}.%(ext)s',
        'keepvideo': False,
        'quiet': True,
        'nocheckcertificate': True,
        'noplaylist': True,
        'concurrent_fragment_downloads': 16,
        'socket_timeout': 30,
        'retries': 15,
        'fragment_retries': 20,
        'skip_unavailable_fragments': False,
        'http_chunk_size': 10485760,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(chzzk_url, download=True)
            downloaded_ext = info_dict.get('ext', 'ts')
            downloaded_raw_path = f"{raw_master_tmpl}.{downloaded_ext}"
    except Exception as e:
        print(f"❌ 멀티스레드 다운로드 오류 발생: {e}")
        return ""

    if not os.path.exists(downloaded_raw_path):
        print("❌ 원본 오디오 마스터 스트림 파일 생성 실패.")
        return ""

    print("⚡ [로컬 가속] 비동기 수집 스트림을 마스터 MP3로 인코딩 중...")
    cmd_master = [
        ffmpeg_bin, '-y',
        '-i', downloaded_raw_path,
        '-acodec', 'libmp3lame',
        '-b:a', '96k',
        master_audio_mp3
    ]
    subprocess.run(cmd_master, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(downloaded_raw_path):
        try: os.remove(downloaded_raw_path)
        except: pass
    print("✅ Base 마스터 MP3 캐시 빌드가 영구 보관되었습니다.")
    return master_audio_mp3

def transcribe_chzzk_audio(audio_path, target_path, model_size="base"):
    if os.path.exists(target_path) and os.path.getsize(target_path) > 10:
        print(f"✨ [STT 대본 캐시 적중] 이미 전사된 원본 전체 대본을 불러옵니다: {target_path}")
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 전체 대본 추출 시작...")
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    try:
        from faster_whisper import WhisperModel
        try:
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print("🚀 [GPU 가속 성공] NVIDIA CUDA 백엔드로 전체 STT 연산을 시작합니다.")
        except Exception as gpu_error:
            print(f"⚠️ GPU 로드 실패 ({gpu_error}). CPU 모드로 전환합니다.")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            print("🐌 [CPU 전환 완료] CPU 환경에서 전체 대본 추출을 진행합니다.")
            
    except ImportError:
        print("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.")
        return ""

    segments, info = model.transcribe(
        audio_path,
        language="ko",
        beam_size=5,
        word_timestamps=False,
        repetition_penalty=1.4,
        compression_ratio_threshold=1.8,
        condition_on_previous_text=False
    )
    
    script_lines = []
    for segment in segments:
        absolute_secs = int(segment.start)
        h = absolute_secs // 3600
        m = (absolute_secs % 3600) // 60
        s = absolute_secs % 60
        
        timestamp_str = f"[{h:02d}:{m:02d}:{s:02d}]"
        text_content = segment.text.strip()
        
        if text_content:
            script_lines.append(f"{timestamp_str} {text_content}")
            print(f"  {timestamp_str} {text_content}")

    raw_script = "\n".join(script_lines)
    
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    print(f"✅ 원본 오프셋 전체 생대본 보관 완료! (보존 경로: {target_path})")
    return raw_script


def generate_chzzk_timeline(input_script, actual_title="VOD제목", chzzk_url="", api_key="", chunk_index=0):
    prompt_path = os.path.join(os.getcwd(), "prompt.txt")
    streamer_info_path = os.path.join(os.getcwd(), "streamer_info.txt")
    
    streamer_stt_list = []
    for line in input_script.split("\n"):
        match = re.match(r"^\[(\d+:\d+:\d+)\]\s+(.*)$", line.strip())
        if match:
            streamer_stt_list.append((timestamp_to_seconds(match.group(1)), match.group(2).strip()))

    base_instruction = (
        "당신은 치지직/인방 다시보기 로그를 가공하는 유능한 유튜브 타임라인 전문 편집자입니다.\n\n"
        "🚨 [시간 정밀 매칭 및 합방 스트리머 표기 규칙]\n"
        "- 언제나 대괄호를 유지하며 대주제와 소주제를 분리한 '[대주제; 소주제]' 포맷을 단락 헤더 라인으로 고수하십시오.\n"
        "- **[합방 멤버 표기 권장]**: 만약 디스코드 합방이나 대화 진행 중인 맥락이라면, 소주제 명칭에 참여 멤버 닉네임을 반드시 명시하십시오. 예: '배틀그라운드(허니츄러스, 아야)'\n"
        "- **[스트리머 반응 최우선화]**: 시청자 채팅창 반응보다 스트리머가 먼저 오디오로 동일한 반응이나 상황 언급을 먼저 했다면, 무조건 스트리머가 소리를 내뱉은 그 최초의 시간(Timestamp) 지점을 찾아 동기화하십시오.\n"
        "- **[문장 초압축]**: 한눈에 들어오도록 각 라인의 content는 10~15자 내외로 극도로 짧게 작성하십시오.\n"
        "- **[내용 중복 금지]**: 각 아이템의 content 본문 내부에 단락 태그를 중복해서 절대 삽입하지 마십시오.\n\n"
        "🚨 [🚨과거 회상 및 썰 풀기 시점 분리 강력 제약]\n"
        "- **현재 실제로 게임 화면을 켜고 플레이하는 것이 아니라, 과거에 있었던 합방이나 옛날 게임 플레이 일화를 단순 대화로 회상하거나 썰을 푸는 상황이라면 절대로 대주제를 '게임 방송'으로 잡지 마십시오.**\n"
        "- 이 경우 대주제는 반드시 **'저스트 채팅'**으로 분류하고, 소주제는 **'과거 합방 썰 풀기'** 혹은 **'지난 방송 회상 및 토크'** 형태로 상황에 맞게 명확히 분리하십시오.\n"
    )

    system_prompt_content = base_instruction
    
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt_content += "\n=====[추가 편집 지침]=====\n" + f.read() + "\n"
            
    if os.path.exists(streamer_info_path):
        with open(streamer_info_path, "r", encoding="utf-8") as f:
            system_prompt_content += "\n=====[스트리머 정보 레퍼런스]=====\n" + f.read()

    user_content = (
        f"영상 제목: {actual_title}\n"
        f"주소: {chzzk_url}\n"
        f"현재 분석 청크 인덱스: {chunk_index} (0이 아니라면 방송 중반부이므로 무조건 '방송 시작 인사' 테마 생성을 금지합니다.)\n\n"
        f"[데이터 원본]\n{input_script}"
    )
    
    max_retries = 5
    retry_delay = 5  
    response_json_text = ""
    time.sleep(1.5)  

    for attempt in range(max_retries):
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt_content,
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    top_p=TOP_P,
                    response_mime_type="application/json",
                    response_schema=TimelineResponse,
                )
            )
            response_json_text = response.text.strip()
            if response_json_text:
                break
        except Exception as e:
            print(f"⚠️ API 연산 처리 재시도 대기 중... (시도: {attempt + 1}/{max_retries})")
            time.sleep(retry_delay)

    if not response_json_text:
        print("❌ 자동 최대 재시도 임계값 초과로 해당 청크구간을 건너뜀.")
        return []

    raw_items = []

    try:
        data = json.loads(response_json_text, strict=False)
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items and isinstance(data, dict) and "timeline" in data:
            items = data.get("timeline", [])

        for item in items:
            gl = item.get("group_large", "").strip()
            topic = item.get("topic", "").strip()
            ts = item.get("timestamp", "").strip()
            wf = item.get("wf", 0)
            wi = item.get("wi", 0)
            content = item.get("content", "").strip()
            
            content = content.replace("🔥", "").strip()

            if any(hallucination in topic or hallucination in content for hallucination in ["리코더", "삑사리", "악기 연주", "피아노"]):
                if "노래" not in gl and "음악" not in gl:
                    continue

            talk_keywords = ["언급", "회상", "기억", "추억", "예전", "지난 방송", "이야기", "썰", "얘기", "토크"]
            if any(word in topic or word in content for word in talk_keywords):
                if gl == "게임 방송":
                    gl = "저스트 채팅"
                    member_match = re.search(r"\(([^)]+)\)", topic)
                    if member_match:
                        topic = f"과거 합방 언급 및 토크({member_match.group(1)})"
                    else:
                        topic = "지난 방송 회상 및 토크"

            current_secs = timestamp_to_seconds(ts)
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 5:
                    ts = seconds_to_timestamp(stt_sec)
                    current_secs = stt_sec
                    break
            
            if chunk_index > 0:
                if gl in ["오프닝", "방송시작", "방송 시작"]: gl = "저스트 채팅"
                if "시작" in topic or "오프닝" in topic or "인사" in topic:
                    topic = "방송 잡담 및 일상 공유"
            
            if any(x in topic for x in ["소통", "시청자 리액션", "리액션", "티키타카"]) and "(" not in topic:
                topic = "방송 잡담 및 일상 공유"
                
            wt = wf + wi
            step = max(1, min(10, round((wt / 100) * 10)))
            
            if step >= 2 or wi >= 25:
                cleaned_content = re.sub(r"\s*\(\s*\d+\s*단계\s*\)\s*", " ", content).strip()
                cleaned_content = cleaned_content.replace("[채팅폭발]", "").strip()
                cleaned_content = re.sub(r"\[\s*[^\]]+;\s*[^\]]+\s*\]", "", cleaned_content).strip()

                pure_text = cleaned_content.replace("🔥", "").strip()
                if not pure_text or re.match(r"^[><!?\s\"']+$", pure_text) or re.match(r"^ㅋ+$", pure_text):
                    continue

                cleaned_content = re.sub(r'ㅋ{4,}', 'ㅋㅋㅋ', cleaned_content)
                cleaned_content = cleaned_content.replace("전개.", "").replace("수행.", "").strip()
                
                raw_items.append({
                    "seconds": current_secs,
                    "timestamp": ts,
                    "group_large": gl,
                    "topic": topic,
                    "content": cleaned_content
                })

    except Exception as parse_error:
        matches = re.findall(r'"group_large"\s*:\s*"([^"]+)"\s*,\s*"topic"\s*:\s*"([^"]+)"\s*,\s*"timestamp"\s*:\s*"([^"]+)"\s*,.*?,"content"\s*:\s*"([^"]+)"', response_json_text, re.DOTALL)
        
        for gl, topic, ts, content in matches:
            gl_val = gl.strip()
            topic_val = topic.strip()
            ts_val = ts.strip()
            content_val = content.strip().replace("🔥", "").strip()

            if any(hallucination in topic_val or hallucination in content_val for hallucination in ["리코더", "삑사리", "악기 연주", "피아노"]):
                if "노래" not in gl_val and "음악" not in gl_val:
                    continue

            talk_keywords = ["언급", "회상", "기억", "추억", "예전", "지난 방송", "이야기", "썰", "얘기", "토크"]
            if any(word in topic_val or word in content_val for word in talk_keywords):
                if gl_val in ["ゲーム 방송", "게임 방송"]:
                    gl_val = "저스트 채팅"
                    member_match = re.search(r"\(([^)]+)\)", topic_val)
                    if member_match:
                        topic_val = f"과거 합방 언급 및 토크({member_match.group(1)})"
                    else:
                        topic_val = "지난 방송 회상 및 토크"

            current_secs = timestamp_to_seconds(ts_val)
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 5:
                    ts_val = seconds_to_timestamp(stt_sec)
                    current_secs = stt_sec
                    break

            if chunk_index > 0:
                if gl_val in ["오프닝", "방송시작", "방송 시작"]: gl_val = "저스트 채팅"
                if "시작" in topic_val or "오프닝" in topic_val or "인사" in topic_val:
                    topic_val = "방송 잡담 및 일상 공유"
            
            if any(x in topic_val for x in ["소통", "시청자 리액션", "리액션", "티키타카"]) and "(" not in topic_val:
                topic_val = "방송 잡담 및 일상 공유"

            cleaned_content = re.sub(r"\s*\(\s*\d+\s*단계\s*\)\s*", " ", content_val).strip()
            cleaned_content = cleaned_content.replace("[채팅폭발]", "").strip()
            cleaned_content = re.sub(r"\[\s*[^\]]+;\s*[^\]]+\s*\]", "", cleaned_content).strip()

            pure_text = cleaned_content.replace("🔥", "").strip()
            if not pure_text or re.match(r"^[><!?\s\"']+$", pure_text) or re.match(r"^ㅋ+$", pure_text):
                continue

            cleaned_content = re.sub(r'ㅋ{4,}', 'ㅋㅋㅋ', cleaned_content)
            cleaned_content = cleaned_content.replace("전개.", "").replace("수행.", "").strip()

            raw_items.append({
                "seconds": current_secs,
                "timestamp": ts_val,
                "group_large": gl_val,
                "topic": topic_val,
                "content": cleaned_content
            })

    return raw_items


def merge_and_format_final_timeline(all_processed_items: list) -> str:
    if not all_processed_items:
        return ""

    all_processed_items.sort(key=lambda x: x["seconds"])

    historical_tags = []  
    
    for item in all_processed_items:
        gl = item["group_large"]
        topic = item["topic"]
        
        norm_gl = re.sub(r"\s+", "", gl).lower()
        norm_topic = re.sub(r"\s+", "", topic).lower()
        pure_topic = re.sub(r"\(.*?\)", "", norm_topic)
        if len(pure_topic) > 3:
            pure_topic = re.sub(r"(게임|방송|플레이|시청|토크|소통|진행|하기)$", "", pure_topic)
            
        current_norm_key = f"{norm_gl};{pure_topic}"
        assigned_header = f"[{gl}; {topic}]"
        
        for past_norm_key, past_header in reversed(historical_tags):
            past_gl = past_norm_key.split(";")[0]
            past_pure_topic = past_norm_key.split(";")[-1]
            
            if norm_gl == past_gl:
                is_topic_similar = (pure_topic == past_pure_topic) or \
                                   (pure_topic in past_pure_topic and len(pure_topic) >= 3) or \
                                   (past_pure_topic in pure_topic and len(past_pure_topic) >= 3)
                
                if is_topic_similar:
                    if "시작" in past_header or "인사" in past_header:
                        if item["seconds"] > 1800:
                            break
                            
                    assigned_header = past_header  
                    break
                    
        if assigned_header == f"[{gl}; {topic}]":
            historical_tags.append((current_norm_key, assigned_header))
            
        item["assigned_header"] = assigned_header

    final_output_lines = []
    current_active_header = None
    seen_entries = set()

    for item in all_processed_items:
        header = item["assigned_header"]
        entry_text = f"[{item['timestamp']}] {item['content']}"
        
        if entry_text in seen_entries:
            continue
        seen_entries.add(entry_text)
        
        if header != current_active_header:
            if current_active_header is not None:
                final_output_lines.append("")  
            final_output_lines.append(header)
            current_active_header = header
            
        final_output_lines.append(entry_text)

    return "\n".join(final_output_lines)