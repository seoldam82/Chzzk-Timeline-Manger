import sys
import os
import json
import warnings
import subprocess
import re
import time
import glob
from datetime import datetime, timedelta
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, Field
from typing import List, Optional

os.environ["OMP_NUM_THREADS"] = "8" 
os.environ["MKL_NUM_THREADS"] = "8"

try:
    embed_bin_dir = os.path.dirname(sys.executable)
    site_packages_dir = os.path.join(embed_bin_dir, "Lib", "site-packages")

    if os.path.exists(site_packages_dir):
        torch_lib = os.path.join(site_packages_dir, "torch", "lib")
        if os.path.exists(torch_lib):
            os.add_dll_directory(torch_lib)

        nvidia_base = os.path.join(site_packages_dir, "nvidia")
        if os.path.exists(nvidia_base):
            for root, dirs, files in os.walk(nvidia_base):
                if any(f.lower().endswith('.dll') for f in files):
                    try:
                        os.add_dll_directory(root)
                    except:
                        pass
                    if root not in os.environ["PATH"]:
                        os.environ["PATH"] = root + os.pathsep + os.environ["PATH"]

        for folder in os.listdir(site_packages_dir):
            if folder.startswith("nvidia_") and "cu12" in folder:
                bin_path = os.path.join(site_packages_dir, folder, "bin")
                if os.path.exists(bin_path):
                    try: os.add_dll_directory(bin_path)
                    except: pass
                    if bin_path not in os.environ["PATH"]:
                        os.environ["PATH"] = bin_path + os.pathsep + os.environ["PATH"]
                    
except Exception as dll_error:
    print(f"⚠️ DLL 디렉토리 자동 등록 중 오류 발생: {dll_error}")

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"
GEMINI_MODEL = "gemini-3.1-flash-lite"      
TEMPERATURE = 0.2  
MAX_OUTPUT_TOKENS = 4000             
TOP_P = 0.95                          
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))

FFMPEG_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
FFMPEG_PATH = os.path.join(PROJECT_ROOT, "ffmpeg", "bin", FFMPEG_EXE)
FFMPEG_BIN_DIR = os.path.dirname(FFMPEG_PATH)
if os.path.exists(FFMPEG_BIN_DIR) and FFMPEG_BIN_DIR not in os.environ["PATH"]:
    os.environ["PATH"] = FFMPEG_BIN_DIR + os.pathsep + os.environ["PATH"]

class TimelineItem(BaseModel):
    group_large: str = Field(
        description="방송 상황의 대분류이자 대주제 (예: 저스트 채팅, 게임 방송, 공지사항, 영도 시청 등)"
    )
    topic: str = Field(
        description=(
            "현재 시간대의 실제 구체적인 대화 주제나 진행 중인 콘텐츠/게임 이름 등의 소주제.\n"
            "🚨 [중요 규칙]: 소주제 명칭에 합방 멤버, 디스코드 대화 참여자 등 다른 스트리머의 닉네임이나 이름, 혹은 관련 괄호 표기를 절대로 포함하지 마십시오.\n"
            "오직 순수한 콘텐츠 명칭이나 게임 제목, 대화 주제만 깔끔하게 작성하십시오. (예: '배틀그라운드', '디스코드 잡담')"
        )
    )
    timestamp: str = Field(description="[HH:MM:SS] 형식의 시간 축 지점")
    wf: int = Field(description="순수 재미 점수 (0 ~ 50) - 시청자 채팅 반응 폭발 강도 및 도배 밀도 기준")
    wi: int = Field(description="내용 중요 점수 (0 ~ 50) - 콘텐츠 전개상 핵심 사건 유무 기준")
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

def sanitize_chzzk_url(url: str) -> str:
    if not url:
        return ""
    markdown_match = re.search(r'\[.*?\]\((.*?)\)', url)
    if markdown_match:
        actual_url = markdown_match.group(1)
        remaining_str = re.sub(r'\[.*?\]\((.*?)\)', '', url).strip()
        if remaining_str and remaining_str not in actual_url:
            if not actual_url.endswith('/'):
                url = actual_url + "/" + remaining_str
            else:
                url = actual_url + remaining_str
        else:
            url = actual_url
    return url.strip().replace("'", "").replace('"', '')

def get_video_duration(chzzk_url):
    chzzk_url = sanitize_chzzk_url(chzzk_url)
    ydl_opts = {'quiet': True, 'nocheckcertificate': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(chzzk_url, download=False)
        return info.get('duration', 0)


def download_chzzk_vod_audio(chzzk_url, vod_id, output_filename="full_vod_audio"):
    chzzk_url = sanitize_chzzk_url(chzzk_url)
    specific_palette_dir = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}")
    
    try:
        os.makedirs(specific_palette_dir, exist_ok=True)
    except PermissionError:
        print(f"❌ [권한 오류] '{specific_palette_dir}' 폴더를 생성할 권한이 없습니다. 관리자 권한으로 실행하세요.")
        return ""
    except Exception as e:
        print(f"❌ [폴더 생성 실패] {e}")
        return ""
    
    master_audio_ts = os.path.join(specific_palette_dir, f"{output_filename}.ts")
    
    if os.path.exists(master_audio_ts) and os.path.getsize(master_audio_ts) > 102400:
        print(f"✨ [오디오 캐시 적중] 전체 원본 TS 파일 로드 완료: {master_audio_ts}")
        return master_audio_ts

    ffmpeg_bin = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else "ffmpeg"

    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        print("❌ VOD 메타데이터 파싱 실패.")
        return ""

    print(f"\n📡 [최초 1회 실행] 멀티스레드 오디오 수집 개시...")
    
    ydl_opts = {
        'format': 'bestaudio/worst',
        'outtmpl': master_audio_ts,
        'keepvideo': False,
        'nocheckcertificate': True,
        'noplaylist': True,
        'concurrent_fragment_downloads': 16,
        'socket_timeout': 60,  
        'retries': 20,
        'fragment_retries': 30,
        'skip_unavailable_fragments': True,
        'http_chunk_size': 5242880,  
        'ffmpeg_location': ffmpeg_bin, 
        'fixup': 'never',
        'postprocessors': [],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(chzzk_url, download=True)
    except Exception as e:
        print(f"⚠️ 멀티스레드 다운로드 중 예외 발생 (확인 프로세스 진행): {e}")

    if not os.path.exists(master_audio_ts):
        extensions = ['*.ts', '*.m4a', '*.aac', '*.mp3']
        found_files = []
        for ext in extensions:
            found_files.extend(glob.glob(os.path.join(specific_palette_dir, f"{output_filename}{ext}")))
        
        if found_files:
            downloaded_file = found_files[0]
            if not downloaded_file.endswith('.ts'):
                print(f"📦 다운로드된 파일 포맷 감지 ({os.path.basename(downloaded_file)}) -> TS 컨테이너로 재정렬 중...")
                cmd_convert = [
                    ffmpeg_bin, '-y', '-i', downloaded_file,
                    '-acodec', 'copy', master_audio_ts
                ]
                subprocess.run(cmd_convert, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try: os.remove(downloaded_file)
                except: pass

    if not os.path.exists(master_audio_ts) or os.path.getsize(master_audio_ts) < 1024:
        print("❌ 원본 오디오 TS 마스터 스트림 파일 생성 실패.")
        return ""

    print("✅ 원본 TS 오디오 캐시 빌드가 영구 보관되었습니다.")
    return master_audio_ts

def transcribe_chzzk_audio(audio_path, target_path, model_size="base"):
    if os.path.exists(target_path) and os.path.getsize(target_path) > 10:
        print(f"✨ [STT 대본 캐시 적중] 이미 전사된 원본 전체 대본을 불러옵니다: {target_path}")
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 안전 분할 전사 시작...")
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    ffmpeg_bin = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else "ffmpeg"
    specific_palette_dir = os.path.dirname(target_path)
    chunk_pattern = os.path.join(specific_palette_dir, "temp_chunk_%03d.ts")
    chunk_length_sec = 3600
    
    for f in glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.ts")):
        try: os.remove(f)
        except: pass

    print("✂️ [오디오 가속] 전체 스트림을 60분 단위 순차 연산 청크로 분할 중...")
    cmd_split = [
        ffmpeg_bin, '-y', '-i', audio_path,
        '-f', 'segment', '-segment_time', str(chunk_length_sec),
        '-acodec', 'copy', chunk_pattern
    ]
    subprocess.run(cmd_split, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    chunk_files = sorted(glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.ts")))
    if not chunk_files:
        print("❌ 분할된 오디오 청크 파일이 존재하지 않습니다.")
        return ""

    try:
        from faster_whisper import WhisperModel
        NUM_CPUS = 8 
        try:
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print("🚀 [GPU 가속 성공] NVIDIA CUDA 백엔드로 순차 STT 연산을 시작합니다.")
        except Exception as gpu_error:
            print(f"⚠️ GPU 로드 실패 ({gpu_error}). CPU 최적화 모드로 전환합니다.")
            model = WhisperModel(
                model_size, 
                device="cpu", 
                compute_type="int8",
                cpu_threads=NUM_CPUS
            )
            print(f"🐌 [CPU 전환 완료] {NUM_CPUS}개 스레드를 활용해 최적화된 대본 추출을 진행합니다.")
            
    except ImportError:
        print("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.")
        return ""

    script_lines = []
    
    for idx, chunk_file in enumerate(chunk_files):
        if os.path.getsize(chunk_file) < 1024:
            continue
            
        current_offset_secs = idx * chunk_length_sec
        print(f"🎙️ [{idx+1}/{len(chunk_files)}] 청크 전사 연산 진행 중: {os.path.basename(chunk_file)}")
        
        segments, info = model.transcribe(
            chunk_file,
            language="ko",
            beam_size=1,
            best_of=1,
            word_timestamps=False,
            repetition_penalty=1.4,
            compression_ratio_threshold=1.8,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=100
            ),
            no_speech_threshold=0.5,
            log_prob_threshold=-1.0
        )
        
        for segment in segments:
            absolute_secs = max(0, int(segment.start) + current_offset_secs - 1)
            h = absolute_secs // 3600
            m = (absolute_secs % 3600) // 60
            s = absolute_secs % 60
            
            timestamp_str = f"[{h:02d}:{m:02d}:{s:02d}]"
            text_content = segment.text.strip()
            
            if text_content:
                script_lines.append(f"{timestamp_str} {text_content}")
                print(f"  {timestamp_str} {text_content}") 

    for chunk_file in chunk_files:
        try: os.remove(chunk_file)
        except: pass

    raw_script = "\n".join(script_lines)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    print(f"✅ 원본 오프셋 전체 생대본 보관 완료! (보존 경로: {target_path})")
    return raw_script

def parse_streamer_info_name(streamer_info_path) -> str:
    if not os.path.exists(streamer_info_path):
        return ""
    try:
        with open(streamer_info_path, "r", encoding="utf-8") as f:
            for line in f:
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("#"):
                    continue
                if ":" in line_strip:
                    return line_strip.split(":")[1].strip()
                return line_strip
    except:
        pass
    return ""

def load_chzzk_streamers_raw_db(filename="chzzk_streamers.txt") -> str:
    db_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(db_path):
        default_content = (
            "# 치지직 스트리머 최종 보정 마스터 DB\n"
            "# 오타/약칭:정식명칭 형태로 적거나 단순 등록할 닉네임을 적어주세요.\n"
            "풍형:풍월량\n동숙형:한동숙\n아니키:한동숙\n칸나:아이리 칸나\n유니:아야츠노 유니\n담유이:담유이\n유이님:담유이\n유이:담유이\n"
            "한동숙\n풍월량\n침착맨\n우왁굳\n랄로\n괴물쥐\n파카\n삼식\n명훈\n다주\n강지\n허니츄러스\n아야츠노 유니\n아이리 칸나\n담유이\n"
        )
        try:
            with open(db_path, "w", encoding="utf-8") as f:
                f.write(default_content)
            print(f"⚙️  [안내] 닉네임 후처리 보정용 파일 '{filename}'이 자동 생성되었습니다.")
        except Exception as e:
            print(f"⚠️ '{filename}' 파일 생성 오류: {e}")
            return ""

    try:
        with open(db_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"⚠️ '{filename}' 데이터 로딩 중 오류 발생: {e}")
        return ""

def load_and_filter_streamers_db(input_script, streamers_db_path="chzzk_streamers.txt", target_streamer="") -> list:
    registered_streamers = []
    raw_db = load_chzzk_streamers_raw_db(streamers_db_path)
    if not raw_db:
        return []

    EXCLUDE_KEYWORDS = {
        "나는", "니야", "반", "뱅", "아야", "연", "이", "이다", "하네", "하세", 
        "나", "너", "우리", "그거", "이거", "저거", "했다", "한다", "형", "님", 
        "아니", "진짜", "그냥", "오늘", "지금", "아이", "하나", "사람", "방송"
    }

    for line in raw_db.split("\n"):
        line_strip = line.strip()
        if not line_strip or line_strip.startswith("#"):
            continue
            
        if ":" in line_strip:
            line_strip = line_strip.split(":")[1].strip()
            
        tokens = re.split(r'[,\s/]+', line_strip)
        for token in tokens:
            token_cleaned = token.strip()
            if not token_cleaned or token_cleaned in EXCLUDE_KEYWORDS or len(token_cleaned) <= 1:
                continue
            if token_cleaned not in registered_streamers:
                registered_streamers.append(token_cleaned)

    collab_context_keywords = ["디코", "디스코드", "보이스", "마이크", "팀원", "같이", "합방", "초대", "들어오", "섭외", "대화", "파티", "경매", "내전", "대회"]
    has_collab_context = any(keyword in input_script for keyword in collab_context_keywords)

    detected_members = {}
    if has_collab_context:
        for streamer_name in registered_streamers:
            if streamer_name == target_streamer:
                continue 
                
            count = len(re.findall(re.escape(streamer_name), input_script))
            if count >= 3:  
                detected_members[streamer_name] = count

    return list(detected_members.keys())

def generate_chzzk_timeline(input_script, chat_script="", actual_title="VOD제목", chzzk_url="", api_key="", chunk_index=0):
    chzzk_url = sanitize_chzzk_url(chzzk_url)

    prompt_path = os.path.join(os.getcwd(), "prompt.txt")
    streamer_info_path = os.path.join(os.getcwd(), "streamer_info.txt")
    streamers_db_path = "chzzk_streamers.txt"
    
    target_streamer = parse_streamer_info_name(streamer_info_path)
    verified_collab_members = load_and_filter_streamers_db(input_script, streamers_db_path, target_streamer)

    streamer_stt_list = []
    for line in input_script.split("\n"):
        match = re.match(r"^\[(\d+:\d+:\d+)\]\s+(.*)$", line.strip())
        if match:
            streamer_stt_list.append((timestamp_to_seconds(match.group(1)), match.group(2).strip()))

    base_instruction = (
        "당신은 치지직/인방 다시보기 로그를 가공하는 유능한 유튜브 타임라인 전문 편집자입니다.\n\n"
        "🚨 [가장 중요한 하이라이트 점수 책정 원칙 - 무조건적인 도입부 가점 배제]\n"
        "- 절대로 영상의 '시작 부분', '청크 파트의 도입부', 또는 특정 시간대([01:00:00], [02:00:00] 등)라는 단지 시간적 이유만으로 관성적인 가점을 주거나 '방송 시작', '오프닝' 등의 불필요한 타임라인 항목을 생성하지 마십시오.\n"
        "- 점수(wf, wi)는 오직 객관적인 재미와 내용의 중요도에 의해서만 엄격하게 결정됩니다. 시청자들의 챗 창 폭발력(ㅋㅋㅋ, ㄷㄷㄷ 등의 도배 밀도), 도네이션 유무, 스트리머의 리액션이 실제로 터진 지점만 높은 점수를 책정해야 합니다.\n"
        "- 재미 점수가 낮거나 평범한 일상 소통, 단순 대기 화면 등 의미 없는 잡담 구간은 과감하게 타임라인 리스트에서 제외하거나 낮게 채점하십시오.\n\n"
        "🚨 [시간 정밀 매칭 및 소주제 작성 절대 규칙]\n"
        "- 언제나 대괄호를 유지하며 대주제와 소주제를 분리한 '[대주제; 소주제]' 포맷을 단락 헤더 라인으로 고수하십시오.\n"
        "- **🚨 [소주제 내 스트리머 닉네임 박제 절대 금지]**: 소주제(topic) 영역에는 합방 멤버나 디코 참여자 등의 스트리머 닉네임을 괄호 포함 어떠한 형태로도 적지 마십시오. 오직 순수한 콘텐츠 명칭이나 제목, 게임 이름만 명료하게 나타내야 합니다. 예시: '배틀그라운드', '디스코드 소통' (절대 '배틀그라운드(스트리머)' 처럼 구성하지 마십시오.)\n"
        "- **[스트리머 반응 최우선화 및 역산 싱크 제약]**: 시청자 채팅창 반응보다 스트리머가 먼저 오디오로 동일한 반응이나 상황 언급을 먼저 했다면, 무조건 스트리머가 소리를 내뱉은 그 최초의 시간(Timestamp) 지점을 찾아 동기화하십시오. 단, 사건 발생 시점보다 발언이 늦어 5~15초 정도의 시간 지연이 발생하는 하이라이트 장면의 경우, 의도적으로 타임스탬프를 3~5초 정도 살짝 앞당겨 실제 상황이 시작되는 시점에 맞추어 타임라인 데이터를 추출하십시오.\n"
        "- **[문장 초압축]**: 한눈에 들어오도록 각 라인의 content는 10~15자 내외로 극도로 짧게 작성하십시오.\n"
        "- **[내용 중복 금지]**: 각 아이템의 content 본문 내부에 단락 태그를 중복해서 절대 삽입하지 마십시오.\n\n"
        "🚨 [과거 회상 및 썰 풀기 시점 분리 강력 제약]\n"
        "- **현재 실제로 게임 화면을 켜고 플레이하는 것이 아니라, 과거에 있었던 합방이나 옛날 게임 플레이 일화를 단순 대화로 회상하거나 썰을 푸는 상황이라면 절대로 대주제를 '게임 방송'으로 잡지 마십시오.**\n"
        "- 이 경우 대주제는 반드시 **'저스트 채팅'**으로 분류하고, 소주제는 **'과거 합방 언급 및 토크'** 혹은 **'지난 방송 회상 및 토크'** 형태로 상황에 맞게 명확히 분리하십시오.\n"
    )

    system_prompt_content = base_instruction
    
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt_content += "\n=====[추가 편집 지침]=====\n" + f.read() + "\n"
            
    if os.path.exists(streamer_info_path):
        with open(streamer_info_path, "r", encoding="utf-8") as f:
            system_prompt_content += "\n=====[스트리머 정보 레퍼런스]=====\n" + f.read()

    collab_text_guide = ", ".join(verified_collab_members) if verified_collab_members else "없음"

    user_content = (
        f"영상 제목: {actual_title}\n"
        f"주소: {chzzk_url}\n"
        f"현재 분석 청크 인덱스: {chunk_index}\n"
        f"🎯 [방송 진행 주인공 스트리머]: {target_streamer}\n"
        f"📢 [참고용 실제 참여/언급 스트리머 목록]: {collab_text_guide}\n"
        f"🚨 [강제 제약 사항]: 소주제(topic)에는 위 목록에 있는 인물을 포함하여 그 어떤 사람의 닉네임도 적지 마십시오.\n\n"
        f"[오디오 STT 데이터 원본]\n{input_script}\n\n"
        f"[시청자 실시간 채팅 데이터 원본]\n{chat_script}"
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
        print("❌ 자동 최대 재시도 임계값 초과로 해당 청크구간을 건너뜜.")
        return []

    raw_items = []

    try:
        data = json.loads(response_json_text, strict=False)
        items = data.get("items", []) if isinstance(data, dict) else []

        for item in items:
            gl = item.get("group_large", "").strip()
            topic = item.get("topic", "").strip()
            ts = item.get("timestamp", "").strip()
            wf = item.get("wf", 0)
            wi = item.get("wi", 0)
            content_val = item.get("content", "").strip()
            topic = re.sub(r"\(.*?\)", "", topic).strip()
            content_val = content_val.replace("🔥", "").strip()

            if any(hallucination in topic or hallucination in content_val for hallucination in ["리코더", "삑사리", "악기 연주", "피아노"]):
                if "노래" not in gl and "음악" not in gl:
                    continue

            talk_keywords = ["언급", "회상", "기억", "추억", "예전", "지난 방송", "이야기", "썰", "얘기", "토크"]
            if gl == "게임 방송" and any(word in topic or word in content_val for word in talk_keywords):
                gl = "저스트 채팅"
                topic = "과거 합방 언급 및 토크"

            current_secs = timestamp_to_seconds(ts)
            best_matched_sec = current_secs
            keyword_candidate = content_val[:4] if len(content_val) >= 4 else content_val
            
            is_highlight = (wf >= 35 or wi >= 35)
            matched_flag = False
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 15 and keyword_candidate in stt_text:
                    best_matched_sec = max(0, stt_sec - 3) if is_highlight else stt_sec
                    matched_flag = True
                    break
                        
            if not matched_flag:
                for stt_sec, stt_text in streamer_stt_list:
                    if abs(current_secs - stt_sec) <= 5:
                        best_matched_sec = max(0, stt_sec - 3) if is_highlight else stt_sec
                        break

            if best_matched_sec != current_secs:
                ts = seconds_to_timestamp(best_matched_sec)
                current_secs = best_matched_sec
            
            if current_secs > 1800:
                if gl in ["오프닝", "방송시작", "방송 시작"]: 
                    gl = "저스트 채팅"
                if any(x in topic for x in ["시작", "오프닝", "인사"]):
                    topic = "방송 잡담 및 일상 공유"
            
            if any(x in topic for x in ["소통", "시청자 리액션", "리액션", "티키타카"]):
                topic = "방송 잡담 및 일상 공유"
                
            if wf + wi >= 40 or wi >= 25:
                cleaned_content = re.sub(r"\s*\(\s*\d+\s*단계\s*\)\s*", " ", content_val).strip()
                cleaned_content = cleaned_content.replace("[채팅폭발]", "").strip()
                cleaned_content = re.sub(r"\[\s*[^\]]+;\s*[^\]]+\s*\]", "", cleaned_content).strip()

                pure_text = cleaned_content.replace("🔥", "").strip()
                if not pure_text or re.match(r"^[><!?\s\"']+$", pure_text) or re.match(r"^ㅋ+$", pure_text):
                    continue

                cleaned_content = re.sub(r'ㅋ{4,}', 'ㅋㅋㅋ', cleaned_content).replace("전개.", "").replace("수행.", "").strip()
                
                raw_items.append({
                    "seconds": current_secs,
                    "timestamp": ts,
                    "group_large": gl,
                    "topic": topic,
                    "content": cleaned_content
                })

    except Exception as parse_error:
        matches = re.findall(r'"group_large"\s*:\s*"([^"]+)"\s*,\s*"topic"\s*:\s*"([^"]+)"\s*,\s*"timestamp"\s*:\s*"([^"]+)"\s*,.*?,"content"\s*:\s*"([^"]+)"', response_json_text, re.DOTALL)
        
        for gl, topic, ts, content_str in matches:
            gl_val = gl.strip()
            topic_val = re.sub(r"\(.*?\)", "", topic.strip()).strip()
            ts_val = ts.strip()
            content_val = content_str.strip().replace("🔥", "").strip()

            if any(hallucination in topic_val or hallucination in content_val for hallucination in ["리코더", "삑사리", "악기 연주", "피아노"]):
                if "노래" not in gl_val and "음악" not in gl_val:
                    continue

            talk_keywords = ["언급", "회상", "기억", "추억", "예전", "지난 방송", "이야기", "썰", "얘기", "토크"]
            if gl_val in ["ゲーム 방송", "게임 방송"] and any(word in topic_val or word in content_val for word in talk_keywords):
                gl_val = "저스트 채팅"
                topic_val = "과거 합방 언급 및 토크"

            current_secs = timestamp_to_seconds(ts_val)
            best_matched_sec = current_secs
            keyword_candidate = content_val[:4] if len(content_val) >= 4 else content_val
            
            matched_flag = False
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 15 and keyword_candidate in stt_text:
                    best_matched_sec = max(0, stt_sec - 3)
                    matched_flag = True
                    break
            
            if not matched_flag:
                for stt_sec, stt_text in streamer_stt_list:
                    if abs(current_secs - stt_sec) <= 5:
                        best_matched_sec = max(0, stt_sec - 3)
                        break

            if best_matched_sec != current_secs:
                ts_val = seconds_to_timestamp(best_matched_sec)
                current_secs = best_matched_sec

            if current_secs > 1800:
                if gl_val in ["오프닝", "방송시작", "방송 시작"]: 
                    gl_val = "저스트 채팅"
                if any(x in topic_val for x in ["시작", "오프닝", "인사"]):
                    topic_val = "방송 잡담 및 일상 공유"
            
            if any(x in topic_val for x in ["소통", "시청자 리액션", "리액션", "티키타카"]):
                topic_val = "방송 잡담 및 일상 공유"

            cleaned_content = re.sub(r"\s*\(\s*\d+\s*단계\s*\)\s*", " ", content_val).strip()
            cleaned_content = cleaned_content.replace("[채팅폭발]", "").strip()
            cleaned_content = re.sub(r"\[\s*[^\]]+;\s*[^\]]+\s*\]", "", cleaned_content).strip()

            pure_text = cleaned_content.replace("🔥", "").strip()
            if not pure_text or re.match(r"^[><!?\s\"']+$", pure_text) or re.match(r"^ㅋ+$", pure_text):
                continue

            cleaned_content = re.sub(r'ㅋ{4,}', 'ㅋㅋㅋ', cleaned_content).replace("전개.", "").replace("수행.", "").strip()

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
            past_gl, past_pure_topic = past_norm_key.split(";", 1)
            
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

def correct_streamer_nicknames_with_gemini(timeline_text: str, api_key: str, db_filename="chzzk_streamers.txt") -> str:
    streamers_db_content = load_chzzk_streamers_raw_db(db_filename)
    
    lines = timeline_text.split("\n")
    processed_lines = []
    current_hour = 0
    
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            processed_lines.append("")
            continue
            
        match_ts = re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\]", line_strip)
        if match_ts:
            current_hour = int(match_ts.group(1))
            if current_hour >= 1:
                line_strip = re.sub(r"방송\s*시작\s*(인사|멘트)?", "방송 잡담 및 소통", line_strip)
                line_strip = re.sub(r"라이브\s*방송\s*잡담", "방송 잡담", line_strip)
        
        if line_strip.startswith("[") and ";" in line_strip and line_strip.endswith("]"):
            line_strip = re.sub(r"\([^)]+\)(?=\s*\])", "", line_strip).strip()
            if current_hour >= 1 and any(x in line_strip for x in ["방송 시작", "오프닝", "방송시작"]):
                line_strip = "[저스트 채팅; 방송 잡담 및 일상 공유]"
                    
        processed_lines.append(line_strip)
        
    intermediate_text = "\n".join(processed_lines)

    system_instruction = (
        "당신은 인터넷 방송 다시보기 타임라인의 구조와 정합성을 검수하고 완성하는 최종 편집 총괄자입니다.\n\n"
        "🚨 [소주제 닉네임 박제 전면 차단 지침]\n"
        "1. 대괄호 내부의 소주제 영역(예: [대주제; 소주제])에 스트리머들의 닉네임이나 괄호 표현이 들어가 있다면 이를 완벽하게 제거하십시오.\n"
        "2. 타임라인 본문 내용(content)에서 오타가 난 명칭은 참고 DB를 바탕으로 자연스럽게 교정할 수 있으나, 소주제 타이틀에는 어떠 한 인물명도 명시되어서는 안 됩니다.\n\n"
        "🚨 [최종 타임라인 정제 제약 사항]\n"
        "1. 제공되는 타임라인의 포맷 구조(대괄호, 시간 스탬프, 세미콜론)는 단 한 글자도 함부로 왜곡하거나 유실시키지 마십시오.\n"
        "2. 방송이 시작된 지 1시간 이상 지난 파트([01:00:00] 이후) 지점 본문 영역에 '방송 시작', '오프닝 인사'와 같은 관성적인 표현이 유실되어 남아있다면, 문맥을 읽어 완전히 소거하거나 '방송 잡담 및 소통' 등으로 매끄럽게 어미를 정돈하십시오.\n"
        "3. 마크다운 코드 블록 마크(```)는 절대 포함하지 말고 순수 타임라인 결과물 텍스트 데이터만 출력하십시오."
    )

    user_prompt = (
        f"===[치지직 스트리머 마스터 DB (참고 사전)]===\n{streamers_db_content}\n\n"
        f"===[교정 대상 타임라인 텍스트]===\n{intermediate_text}\n\n"
        "위 타임라인 텍스트의 소주제 타이틀 영역에서 괄호 및 모든 닉네임 표기를 완벽히 제거하고 포맷을 깔끔하게 완성해 주세요."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1, 
                max_output_tokens=MAX_OUTPUT_TOKENS,
                top_p=0.95,
            )
        )
        corrected_text = response.text.strip()
        if corrected_text:
            final_lines = []
            for line in corrected_text.split("\n"):
                if line.strip().startswith("[") and ";" in line and line.strip().endswith("]"):
                    line = re.sub(r"\s*\([^)]+\)", "", line)
                final_lines.append(line)
            return "\n".join(final_lines)
    except Exception as e:
        print(f"⚠️ [Gemini 연산 실패] AI 검수 중 오류가 발생하여 1차 구조 정리본을 반환합니다: {e}")
    
    return intermediate_text