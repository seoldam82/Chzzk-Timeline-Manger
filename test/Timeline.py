import sys
import os
import json
import warnings
import subprocess
import re
import time
import math
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

SHOW = True
def log(message, level="INFO", show=True):
    if show:
        print(f"{message}")

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
    log(f"⚠️ DLL 디렉토리 자동 등록 중 오류 발생: {dll_error}", level="WARNING", show=SHOW)

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
    timestamp: str = Field(description="hh:mm:ss 형식의 시간 축 지점")
    wf: float = Field(
        description=(
            "순수 재미 점수 (0.00 ~ 50.00)입니다.\n"
            "🚨 [점수 책정 절대 규칙]: 정수나 소수점 첫째 자리(.0, .5 등) 매기기 절대 금지.\n"
            "반드시 시청자 채팅 폭발 강도를 세밀하게 분석하여 [무조건 소수점 둘째 자리(예: 42.13, 35.87)]까지 정밀하게 계산된 실수 값을 입력하십시오.\n"
            "⚠️ 같은 리스트(현재 1시간 구간) 내에서 동일한 wf 값이 중복되어 출현하는 것을 방지하기 위해, 소수점 둘째 자리를 최대한 무작위적이고 다양하게 분산하십시오."
        )
    )
    
    wi: float = Field(
        description=(
            "내용 중요 점수 (0.000 ~ 50.000)입니다.\n"
            "🚨 [동점자 방지 절대 규칙]: 재미 점수(wf)와 더했을 때(wf + wi) 전체 방송에서 최종 합계 점수가 겹치는 것을 원천 차단해야 합니다.\n"
            "이를 위해 중요 점수(wi)는 대충 매기지 말고, 반드시 [소수점 셋째 자리(예: 25.137, 34.821)]까지 극도로 쪼개진 정밀 실수 값을 생성하십시오.\n"
            "⚠️ 끝자리가 .000, .500, .250 등으로 딱 떨어지게 끝내지 말고, .142, .789 처럼 지저분하고 고유한 숫자를 부여하여 현재 리스트 내에서 다른 아이템들과 최종 합계 순위가 공동 순위(동점)가 되지 않도록 하십시오."
        )
    )
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
        log(f"\n⚙️  [안내] 프로젝트 폴더에 '{CONFIG_FILE}' 파일이 생성되었습니다.", show=SHOW)
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
        log(f"❌ [JSON 파싱 실패] config.json 파일을 읽는 중 오류 발생: {e}", level="ERROR", show=SHOW)
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
        log(f"❌ [권한 오류] '{specific_palette_dir}' 폴더를 생성할 권한이 없습니다. 관리자 권한으로 실행하세요.")
        return ""
    except Exception as e:
        log(f"❌ [폴더 생성 실패] {e}")
        return ""
    
    master_audio_ts = os.path.join(specific_palette_dir, f"{output_filename}.ts")
    
    if os.path.exists(master_audio_ts) and os.path.getsize(master_audio_ts) > 102400:
        log(f"✨ [오디오 캐시 적중] 전체 원본 TS 파일 로드 완료: {master_audio_ts}")
        return master_audio_ts

    ffmpeg_bin = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else "ffmpeg"

    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        log("❌ VOD 메타데이터 파싱 실패.")
        return ""

    log(f"\n📡 [최초 1회 실행] 멀티스레드 오디오 수집 개시...")
    
    ydl_opts = {
        'format': 'worstaudio/worst',
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
        log(f"⚠️ 멀티스레드 다운로드 중 예외 발생 (확인 프로세스 진행): {e}", level="WARNING", show=SHOW)

    if not os.path.exists(master_audio_ts):
        extensions = ['*.ts', '*.m4a', '*.aac', '*.mp3']
        found_files = []
        for ext in extensions:
            found_files.extend(glob.glob(os.path.join(specific_palette_dir, f"{output_filename}{ext}")))
        
        if found_files:
            downloaded_file = found_files[0]
            if not downloaded_file.endswith('.ts'):
                log(f"📦 다운로드된 파일 포맷 감지 ({os.path.basename(downloaded_file)}) -> TS 컨테이너로 재정렬 중...")
                cmd_convert = [
                    ffmpeg_bin, '-y', '-i', downloaded_file,
                    '-acodec', 'copy', master_audio_ts
                ]
                subprocess.run(cmd_convert, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try: os.remove(downloaded_file)
                except: pass

    if not os.path.exists(master_audio_ts) or os.path.getsize(master_audio_ts) < 1024:
        log("❌ 원본 오디오 TS 마스터 스트림 파일 생성 실패.", level="ERROR", show=SHOW)
        return ""

    log("✅ 원본 TS 오디오 캐시 빌드가 영구 보관되었습니다.", show=SHOW)
    return master_audio_ts

def transcribe_chzzk_audio(audio_path, target_path, model_size="base"):
    if os.path.exists(target_path) and os.path.getsize(target_path) > 10:
        log(f"✨ [STT 대본 캐시 적중] 이미 전사된 원본 전체 대본을 불러옵니다: {target_path}", show=SHOW)
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()

    log(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 안전 분할 전사 시작...", show=SHOW)
    if not os.path.exists(audio_path):
        log("❌ 분석할 오디오 파일이 존재하지 않습니다.", level="ERROR", show=SHOW)
        return ""

    ffmpeg_bin = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else "ffmpeg"
    specific_palette_dir = os.path.dirname(target_path)
    chunk_pattern = os.path.join(specific_palette_dir, "temp_chunk_%03d.ts")
    chunk_length_sec = 3600
    
    for f in glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.ts")):
        try: os.remove(f)
        except: pass

    log("✂️ [오디오 가속] 전체 스트림을 60분 단위 순차 연산 청크로 분할 중...", show=SHOW)
    cmd_split = [
        ffmpeg_bin, '-y', '-i', audio_path,
        '-f', 'segment', '-segment_time', str(chunk_length_sec),
        '-acodec', 'copy', chunk_pattern
    ]
    subprocess.run(cmd_split, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    chunk_files = sorted(glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.ts")))
    if not chunk_files:
        log("❌ 분할된 오디오 청크 파일이 존재하지 않습니다.", level="ERROR", show=SHOW)
        return ""

    weights_dir = os.path.abspath(os.path.join(CURRENT_DIR, "..", "weights"))

    try:
        from faster_whisper import WhisperModel
        NUM_CPUS = 8 
        try:
            model = WhisperModel(
                model_size, 
                device="cuda", 
                compute_type="float16",
                download_root=weights_dir
            )
            log(f"🚀 [GPU 가속 성공] NVIDIA CUDA 백엔드로 순차 STT 연산을 시작합니다. (모델 저장 위치: {weights_dir})")
        except Exception as gpu_error:
            log(f"⚠️ GPU 로드 실패 ({gpu_error}). CPU 최적화 모드로 전환합니다.", level="WARNING", show=SHOW)
            model = WhisperModel(
                model_size, 
                device="cpu", 
                compute_type="int8",
                cpu_threads=NUM_CPUS,
                download_root=weights_dir
            )
            log(f"🐌 [CPU 전환 완료] {NUM_CPUS}개 스레드를 활용해 최적화된 대본 추출을 진행합니다. (모델 저장 위치: {weights_dir})", show=SHOW)
            
    except ImportError:
        log("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.", level="ERROR", show=SHOW)
        return ""

    script_lines = []
    
    for idx, chunk_file in enumerate(chunk_files):
        if os.path.getsize(chunk_file) < 1024:
            continue
            
        current_offset_secs = idx * chunk_length_sec
        log(f"🎙️ [{idx+1}/{len(chunk_files)}] 청크 전사 연산 진행 중: {os.path.basename(chunk_file)}")
        
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
                log(f"  {timestamp_str} {text_content}", show=SHOW)

    for chunk_file in chunk_files:
        try: os.remove(chunk_file)
        except: pass

    raw_script = "\n".join(script_lines)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    log(f"✅ 원본 오프셋 전체 생대본 보관 완료! (보존 경로: {target_path})", show=SHOW)
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
            log(f"⚙️  [안내] 닉네임 후처리 보정용 파일 '{filename}'이 자동 생성되었습니다.")
        except Exception as e:
            log(f"⚠️ '{filename}' 파일 생성 오류: {e}", level="WARNING", show=SHOW)
            return ""

    try:
        with open(db_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log(f"⚠️ '{filename}' 데이터 로딩 중 오류 발생: {e}", level="WARNING", show=SHOW)
        return ""

def load_and_filter_streamers_db(input_script, streamers_db_path="chzzk_streamers.txt", target_streamer="") -> list:
    registered_streamers = []
    raw_db = load_chzzk_streamers_raw_db(streamers_db_path)
    if not raw_db:
        return []
    
    EXCLUDE_KEYWORDS = {
        "나는", "니야", "반", "연", "이", "이다", "하네요", "하세", 
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
        "당신은 치지직/인방 다시보기 로그를 가공하는 유능한 영상 타임라인 전문 편집자입니다.\n\n"
        
        "🚨 [최우선 분석 순위: 클립 생성 밀집 구간(핫스팟) 현미경 분할 규칙]\n"
        "- 대본 상단(`==== [🚨 타임라인 가산점 및 클립 밀도 절대 규칙] ====`)에 명시된 클립 밀집도 가이드라인을 최우선 순위로 스캔하십시오.\n"
        "- **대량의 클립이 밀집되어 생성된 '핫스팟 구역(밀집 구간)'은 시청자 피드백이 폭발한 핵심 하이라이트이므로, 절대로 하나의 아이템으로 크게 뭉뚱그려 요약해서는 안 됩니다.**\n"
        "- 해당 밀집 구간 내부에서는 **최소 30초~1분 단위로 타임라인을 현미경처럼 조밀하게 쪼개어**, 스트리머의 리액션 변화, 세부 대화 상황, 채팅창 폭발 지점을 누락 없이 촘촘하게 반영하십시오.\n"
        "- 가이드라인에 안내된 클립 점수 배점(조회수별 가산점)을 재미 점수(wf)와 중요도 점수(wi)에 무조건 더하여 가중치를 극대화하십시오.\n\n"

        "🚨 [가장 중요한 하이라이트 점수 책정 원칙 - 무조건적인 도입부 가점 배제]\n"
        "- 절대로 영상의 '시작 부분', '청크 파트의 도입부', 또는 특정 시간대([01:00:00], [02:00:00] 등)라는 단지 시간적 이유만으로 관성적인 가점을 주거나 '방송 시작', '오프닝' 등의 불필요한 타임라인 항목을 생성하지 마십시오.\n"
        "- 점수(wf, wi)는 오직 객관적인 재미와 내용의 중요도에 의해서만 엄격하게 결정됩니다. 시청자들의 챗 창 폭발력(ㅋㅋㅋ, ㄷㄷㄷ 등의 도배 밀도), 도네이션 유무, 스트리머의 리액션이 실제로 터진 지점만 높은 점수를 책정해야 합니다.\n"
        "- 재미 점수가 낮거나 평범한 일상 소통, 단순 대기 화면 등 의미 없는 잡담 구간은 과감하게 타임라인 리스트에서 제외하거나 낮게 채점하십시오.\n\n"
        
        "🚨 [시간 정밀 매칭 및 소주제 작성 절대 규칙]\n"
        "- 언제나 대괄호를 유지하며 대주제와 소주제를 분리한 '[대주제; 소주제]' 포맷을 단락 헤더 라인으로 고수하십시오.\n"
        "- **🚨 [소주제 내 스트리머 닉네임 박제 절대 금지]**: 소주제(topic) 영역에는 합방 멤버나 디코 참여자 등의 스트리머 닉네임을 괄호 포함 어떠한 형태로도 적지 마십시오. 오직 순수한 콘텐츠 명칭이나 제목, 게임 이름만 명료하게 나타내야 합니다. 예시: '배틀그라운드', '디스코드 잡담' (절대 '배틀그라운드(스트리머)' 처럼 구성하지 마십시오.)\n"
        "- **[의미론적 대사 시작점 매칭 제약]**:\n"
        "  * 타임라인 대사나 상황을 분석할 때 스트리머가 내뱉은 불필요한 필터 워드(Filler word: 어, 음, 아, 그, 있잖아 등)나 말더듬 구간의 시간대는 완전히 배제하십시오.\n"
        "  * 반드시 실질적인 핵심 의미나 본문 상황이 시작되는 첫 단어(명사, 동사 등 실제 단어)의 시작 오디오 시점을 기준으로 정확하게 타임스탬프 후보를 판단하십시오.\n"
        "- **[문장 초압축 및 명사형 종결 절대 규칙]**:\n"
        "  * 한눈에 들어오도록 각 라인의 content는 10~15자 내외로 극도로 짧게 작성하십시오.\n"
        "  * 상황을 설명할 때 '~하는 모습', '~하는 중', '~함'과 같은 서술형 종결 어미를 절대 사용하지 말고, 명사 또는 명사구 형태로 간결하게 끝마치십시오.\n"
        "  * 올바른 예시: '허접 상대 압살', '디코방 음질 불평', '적 처치 후 도발', '솔로 랭크 캐리 승리'\n"
        "  * 잘못된 예시: '허접 상대 압살하는 모습', '디코방 음질이 안 좋다고 불평함', '적 처치하고 도발하는 중'\n"
        "- **[내용 중복 금지]**: 각 아이템의 content 본문 내부에 단락 태그를 중복해서 절대 삽입하지 마십시오.\n"
        "- '출근', '퇴근'이라는 단어 대신 '뱅온', '방종'으로 표현하십시오.\n"
        
        "🚨 [과거 회상 및 썰 풀기 시점 분리 강력 제약]\n"
        "- **현재 실제로 게임 화면을 켜고 플레이하는 것이 아니라, 과거에 있었던 합방이나 옛날 게임 플레이 일화를 단순 대화로 회상하거나 썰을 푸는 상황이라면 절대로 대주제를 '게임 방송'으로 잡지 마십시오.**\n"
        "- 이 경우 대주제는 반드시 **'저스트 채팅'**으로 분류하고, 소주제는 **'과거 합방 언급 및 토크'** 혹은 **'지난 방송 회상 및 토크'** 형태로 상황에 맞게 명확히 분리하십시오.\n\n"
        
        "🚨 [마스터 DB 기반 주어(닉네임) 유연성 제약]\n"
        "- 타임라인 본문 내용(content)을 구성할 때, 막연하고 모호한 일반 명사인 '스트리머'라는 단어는 최대한 지양하십시오.\n"
        "- 제공된 [방송 진행 주인공 스트리머] 및 [참고용 실제 참여/언급 스트리머 목록]을 적극 참고하여, 주체적으로 행동하거나 핵심 멘트를 친 인물이 누구인지 명확히 구별하십시오.\n"
        "- 인물 식별이 필요하다고 판단되는 하이라이트 상황(단독 캐리, 솔로 플레이 에피소드 등)에서는 반드시 '주인공 스트리머 닉네임'을 주어로 명시하여 문장을 작성하되, 명사 형태로 끝맺으십시오. (예: '풍월량 솔로 캐리로 게임 승리')\n"
        "- 다인 합방 또는 디스코드 소통 상황에서 특정 타 스트리머가 리액션을 주도했거나 티키타카가 발생한 경우, 해당 스트리머 목록 사전을 대조하여 대상 스트리머의 정식 닉네임을 주어로 명확히 지정하되, 이 역시 명사형으로 간결하게 작성하십시오. (예: '삼식의 갑작스러운 뇌절 리액션')\n"
        
        "🚨 [소주제(topic) 작성 규칙 - 필수 제약 사항]\n"
        "1. topic(소주제)은 현재 진행 중인 콘텐츠나 게임의 고유 명사 및 고유 제목만을 명확하게 작성하세요.\n"
        "2. ⚠️ 금지 사항: topic 항목을 작성할 때 단어 맨 뒤에 '게임', '플레이', '방송', '진행', '하기'와 같은 불필요한 서술성 명사나 접미사를 절대 붙이지 마세요.\n"
        "3. 스트리머가 동일한 게임을 계속하고 있다면, 게임 안에서 세부 상황(잠입, 보스전 등)이 바뀌더라도 topic 명칭은 최초에 지정한 고유 명사로 완벽하게 동일하게 유지해야 합니다.\n"
    
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
                    temperature=0.05,
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
            log(f"⚠️ API 연산 처리 재시도 대기 중... (시도: {attempt + 1}/{max_retries})", level="WARNING", show=SHOW)
            time.sleep(retry_delay)

    if not response_json_text:
        log("❌ 자동 최대 재시도 임계값 초과로 해당 청크구간을 건너뜜.", level="ERROR", show=SHOW)
        return []

    try:
        vod_match = re.search(r"video/(\d+)", chzzk_url)
        vod_id = vod_match.group(1) if vod_match else "unknown_vod"
        output_dir = os.path.join(os.getcwd(), "TL_VOD", vod_id)
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        combined_json_path = os.path.join(output_dir, "raw_responses_combined.json")
        
        combined_data = {}
        if chunk_index > 0 and os.path.exists(combined_json_path):
            try:
                with open(combined_json_path, "r", encoding="utf-8") as rf:
                    combined_data = json.load(rf)
            except Exception:
                combined_data = {}

        try:
            parsed_raw = json.loads(response_json_text, strict=False)
            combined_data[f"chunk_{chunk_index}"] = parsed_raw
        except Exception:
            combined_data[f"chunk_{chunk_index}"] = {"raw_text": response_json_text}

        with open(combined_json_path, "w", encoding="utf-8") as wf_json:
            json.dump(combined_data, wf_json, ensure_ascii=False, indent=4)
        log(f"💾 [통합 백업] 후처리 전 원본 JSON 누적 완료 -> {combined_json_path}", show=SHOW)
        
    except Exception as save_err:
        log(f"⚠️ 후처리 전 JSON 통합 저장 중 오류 발생 (진행은 유지됨): {save_err}", level="WARNING", show=SHOW)

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
            
            is_critical_moment = (wf >= 42 or "킬" in content_val or "승리" in content_val or "압살" in content_val or "클리어" in content_val or "전멸" in content_val)
            is_general_summary = (wi >= 35 and wf < 30)

            matched_flag = False
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 15 and keyword_candidate in stt_text:
                    cleaned_stt = re.sub(r"^(어|음|아|그|그게|있잖아|어음)\s+", "", stt_text).strip()
                    if cleaned_stt != stt_text and len(cleaned_stt) > 0:
                        char_diff = len(stt_text) - len(cleaned_stt)
                        est_delay = max(0.0, char_diff * 0.25)
                        stt_sec = stt_sec + est_delay

                    if is_critical_moment:
                        best_matched_sec = stt_sec - 0.5
                    elif is_general_summary:
                        best_matched_sec = max(0.0, stt_sec - 3.0)
                    else:
                        best_matched_sec = max(0.0, stt_sec - 1.5)
                    
                    matched_flag = True
                    break
                        
            if not matched_flag:
                for stt_sec, stt_text in streamer_stt_list:
                    if abs(current_secs - stt_sec) <= 5:
                        cleaned_stt = re.sub(r"^(어|음|아|그|그게|있잖아|어음)\s+", "", stt_text).strip()
                        if cleaned_stt != stt_text and len(cleaned_stt) > 0:
                            char_diff = len(stt_text) - len(cleaned_stt)
                            est_delay = max(0.0, char_diff * 0.25)
                            stt_sec = stt_sec + est_delay

                        if is_critical_moment:
                            best_matched_sec = stt_sec - 0.5
                        elif is_general_summary:
                            best_matched_sec = max(0.0, stt_sec - 3.0)
                        else:
                            best_matched_sec = max(0.0, stt_sec - 1.5)
                        break

            if best_matched_sec != current_secs:
                ts = seconds_to_timestamp(int(best_matched_sec))
                current_secs = int(best_matched_sec)
            
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
            
            is_critical_moment = (content_val.find("킬") != -1 or content_val.find("승리") != -1 or content_val.find("압살") != -1 or content_val.find("클리어") != -1)
            is_general_summary = (topic_val.find("토크") != -1 or topic_val.find("공유") != -1 or topic_val.find("잡담") != -1)

            matched_flag = False
            for stt_sec, stt_text in streamer_stt_list:
                if abs(current_secs - stt_sec) <= 15 and keyword_candidate in stt_text:
                    cleaned_stt = re.sub(r"^(어|음|아|그|그게|있잖아|어음)\s+", "", stt_text).strip()
                    if cleaned_stt != stt_text and len(cleaned_stt) > 0:
                        char_diff = len(stt_text) - len(cleaned_stt)
                        est_delay = max(0.0, char_diff * 0.25)
                        stt_sec = stt_sec + est_delay

                    if is_critical_moment:
                        best_matched_sec = stt_sec - 0.5
                    elif is_general_summary:
                        best_matched_sec = max(0.0, stt_sec - 3.0)
                    else:
                        best_matched_sec = max(0.0, stt_sec - 1.5)
                    matched_flag = True
                    break
            
            if not matched_flag:
                for stt_sec, stt_text in streamer_stt_list:
                    if abs(current_secs - stt_sec) <= 5:
                        cleaned_stt = re.sub(r"^(어|음|아|그|그게|있잖아|어음)\s+", "", stt_text).strip()
                        if cleaned_stt != stt_text and len(cleaned_stt) > 0:
                            char_diff = len(stt_text) - len(cleaned_stt)
                            est_delay = max(0.0, char_diff * 0.25)
                            stt_sec = stt_sec + est_delay

                        if is_critical_moment:
                            best_matched_sec = stt_sec - 0.5
                        elif is_general_summary:
                            best_matched_sec = max(0.0, stt_sec - 3.0)
                        else:
                            best_matched_sec = max(0.0, stt_sec - 1.5)
                        break

            if best_matched_sec != current_secs:
                ts_val = seconds_to_timestamp(int(best_matched_sec))
                current_secs = int(best_matched_sec)

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

    hot_items = [item for item in all_processed_items if "🔥" in item.get("content", "")]
    limit = int(len(all_processed_items) * 0.20)
    allowed_hot_items = set(hot_items[:limit]) if limit > 0 else set()

    historical_tags = [] 
    
    for item in all_processed_items:
        gl = item["group_large"].strip()
        topic = item["topic"].strip()
        norm_topic = re.sub(r"\s+", "", topic).lower()
        pure_topic = re.sub(r"[\(\)\_\-\[\]\.\,\?\!]", "", norm_topic) 
        assigned_header = f"[{gl}; {topic}]"
        
        for past_header, past_pure_topic in reversed(historical_tags):
            is_topic_similar = False
            
            if (pure_topic == past_pure_topic) or \
               (pure_topic in past_pure_topic and len(pure_topic) >= 3) or \
               (past_pure_topic in pure_topic and len(past_pure_topic) >= 3):
                is_topic_similar = True
                
            elif len(pure_topic) >= 2 and len(past_pure_topic) >= 2:
                set_current = set(pure_topic)
                set_past = set(past_pure_topic)
                intersection = set_current.intersection(set_past)
                
                min_len = min(len(set_current), len(set_past))
                if min_len > 0 and (len(intersection) / min_len) >= 0.50:
                    is_topic_similar = True
            
            if is_topic_similar:
                if "seconds" in item and item["seconds"] > 1800:
                    if "시작" in past_header or "인사" in past_header:
                        break
                assigned_header = past_header
                break
                    
        if assigned_header == f"[{gl}; {topic}]":
            historical_tags.append((assigned_header, pure_topic))
            
        item["assigned_header"] = assigned_header
        
    final_output_lines = []
    current_active_header = None
    seen_entries = set()

    for item in all_processed_items:
        header = item["assigned_header"].strip()
        content = item['content'].strip()
        
        if "🔥" in content and item not in allowed_hot_items:
            content = content.replace("🔥", "").strip()
            
        content = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", content).strip()
        content = re.sub(r"^\d{2}:\d{2}:\d{2}\s*", "", content).strip()
        content = re.sub(r"^[🎬🔥\s]+", "", content).strip()
        entry_text = f"{item['timestamp']} {content}"
        
        if entry_text in seen_entries:
            continue
        
        if header != current_active_header:
            if final_output_lines:
                final_output_lines.append("")
            final_output_lines.append(header)
            current_active_header = header
            
        final_output_lines.append(entry_text)
        seen_entries.add(entry_text)

    return "\n".join(final_output_lines).strip()

def Final_Processing(timeline_text: str, api_key: str, db_filename="chzzk_streamers.txt", game_category=[]) -> str:
    db_path = os.path.join(os.getcwd(), db_filename)
    streamers_db_content = "등록된 데이터 없음"
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                streamers_db_content = f.read().strip()
        except:
            pass

    weight_sec_map = {}
    
    try:
        search_dir = os.path.join(os.getcwd(), "TL_VOD")
        json_files = glob.glob(os.path.join(search_dir, "*", "raw_responses_combined.json"))
        if json_files:
            latest_json_path = max(json_files, key=os.path.getmtime)
            if os.path.exists(latest_json_path):
                with open(latest_json_path, "r", encoding="utf-8") as jf:
                    combined_data = json.load(jf)
                for chunk_key, chunk_val in combined_data.items():
                    for it in chunk_val.get("items", []):
                        ts_str = it.get("timestamp", "").strip().replace("[", "").replace("]", "")
                        time_match = re.match(r"^(\d{2}):(\d{2}):(\d{2})", ts_str)
                        if time_match:
                            h, m, s = map(int, time_match.groups())
                            sec_val = h * 3600 + m * 60 + s
                            wf = float(it.get("wf", 0.0))
                            wi = float(it.get("wi", 0.0))
                            total_score = float(f"{wf + wi:.3f}")
                            weight_sec_map[sec_val] = {
                                "original_ts": ts_str,
                                "total_weight": total_score,
                                "topic": it.get("topic", "")
                            }
    except Exception as j_err:
        log(f"❌ JSON 데이터 로딩 중 오류 발생: {j_err}", level="ERROR", show=SHOW)

    lines = timeline_text.split("\n")
    line_meta_table = {} 
    clean_contents_list = []
    
    line_counter = 0
    
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
            
        if (line_strip.startswith("[") and ";" in line_strip) or (line_strip.endswith("]") and ";" in line_strip):
            clean_contents_list.append(line_strip)
            continue
            
        match_ts = re.search(r"\[?(\d{2}):(\d{2}):(\d{2})\]?", line_strip)
        if match_ts:
            h, m, s = match_ts.group(1), match_ts.group(2), match_ts.group(3)
            txt_sec = int(h) * 3600 + int(m) * 60 + int(s)
            current_hour = int(h)
            saved_icon = "🎬" if "🎬" in line_strip else ""
            matched_json_data = {"original_ts": f"{h}:{m}:{s}", "total_weight": 0.000, "topic": ""}
            min_delta = float('inf')
            
            for j_sec, j_data in weight_sec_map.items():
                delta = abs(j_sec - txt_sec)
                if delta < min_delta:
                    min_delta = delta
                    matched_json_data = j_data
            
            marker_id = f"LINE_{line_counter}"
            line_counter += 1
            
            line_meta_table[marker_id] = {
                "timestamp": matched_json_data["original_ts"], 
                "icon": saved_icon,
                "weight": matched_json_data["total_weight"],
                "is_clip": "클립" in matched_json_data["topic"]
            }
            
            raw_tail = line_strip[match_ts.end():].strip()
            raw_tail = re.sub(r"^[\]\s🔥🎬]+", "", raw_tail).strip()
            pure_content = re.sub(r"\s+", " ", raw_tail).strip()
            
            if current_hour >= 1:
                pure_content = re.sub(r"방송\s*시작\s*(인사|멘트)?", "방송 잡담 및 소통", pure_content)
                pure_content = re.sub(r"라이브\s*방송\s*잡담", "방송 잡담", pure_content)
                
            clean_contents_list.append(f"{pure_content} [{marker_id}]")
        else:
            clean_contents_list.append(line_strip)
            
    
    log(f"게임 카테고리: {game_category}", level="INFO", show=False)
    intermediate_text = "\n".join(clean_contents_list)
    system_instruction = (
        "당신은 인터넷 방송 다시보기 타임라인 본문의 맞춤법과 어색한 문장 구조를 종합 검수하는 최상위 전문 편집자입니다.\n\n"
        "🚨 [절대 규칙: 고유 마커 마크 고정 및 유실 금지]\n"
        "- 문장의 맨 뒤에 붙어 있는 `[LINE_0]`, `[LINE_1]` 형태의 고유 마커 꼬리표는 매칭 시스템의 인덱스 키이므로 **절대 삭제, 수정, 변경, 누락하거나 자리를 바꾸지 마십시오.**\n"
        "- 교정 완료 후 출력할 때도 문장 끝부분의 `[LINE_XX]` 포맷을 완벽하게 그대로 유지한 채 출력해야 합니다.\n\n"
        "🚨 [핵심 임무: 본문 압축 규칙]\n"
        "- 대괄호로 감싸진 대분류 분류 헤더의 오타를 바로잡고 구조를 깨뜨리지 마십시오.\n"
        "- 각 타임라인 줄의 본문 설명 문장은 10~15자 내외의 명사 또는 명사구 형태로 종결되도록 문장을 압축하여 정제하십시오. (~함, ~하는 모습 등 금지)\n"
        "- 합방 멤버나 스트리머의 불필요한 닉네임, 중복 수식어를 지우고 가독성을 극대화하십시오.\n\n"
        "🚨 [금지어 세부 규칙]\n"
        "1. 문장 내에 '언급', '언급함' 이라는 단어는 절대 금지합니다. 문맥에 따라 '이야기', '토크', '소통' 등으로 완벽하게 우회하십시오.\n"
        "2. 문장 내에 '자살', '자해'라는 단어는 단 한 번도 절대 사용하지 마십시오.\n"
        "3. 마크다운 코드 블록 마크(```)는 절대 포함하지 말고 순수 텍스트만 출력하십시오.\n\n"
        "🚨 [대주제 판별 및 게임 카테고리 매칭 규칙]\n"
        "- 분석 중인 영상의 대주제가 '게임 방송'인 경우에만 아래의 최종 헤더 매칭 작업을 수행하십시오.\n"
        "- 입력 데이터로 주어지는 [소주제 텍스트]의 핵심 맥락(예: 잠입 액션)을 파악하십시오.\n\n"
        "🚨 [제공된 카테고리 참조 및 일관성 최우선 규칙]\n"
        f"- [제공된 후보 리스트]: {game_category}\n"
        "- **[최우선 사항]** 최종 헤더를 결정할 때 임의로 게임 명칭을 지어내지 말고, **반드시 위에 제공된 후보 리스트 안에 존재하는 게임 명칭을 최우선적으로 매칭하여 사용**하십시오.\n\n"
        "🚨 [예외 처리 및 예외적 채택 규칙]\n"
        "- **[예외 1. 명확한 게임명 존재]** 만약 [소주제 텍스트] 내에 이미 명확한 게임 이름이 직접적으로 적혀 있는 경우에는, 후보 리스트와 무관하게 해당 게임 이름을 그대로 최종 헤더에 채택하십시오.\n"
        "- **[예외 2. 키워드 완전 불일치]** 제공된 후보 리스트의 모든 값이 [소주제 텍스트]의 맥락과 전혀 연관이 없고 완전히 어긋나는 극단적인 경우에만, 억지로 짜맞추지 말고 소주제에 부합하는 실제 다른 게임 이름을 유추하여 최종 헤더에 채택하십시오.\n\n"
        "🚨 [장르 비교 및 최종 헤더 구조 매칭 규칙]\n"
        "- 위의 예외 사항에 해당하지 않는 일반적인 경우, 후보 리스트 내 게임들의 장르 키워드와 [소주제 텍스트]를 비교하여 가장 연관성이 높은 단 하나의 게임명을 선택하십시오.\n"
        "- 최종 출력은 선택되거나 유추된 게임명을 사용하여 반드시 `[게임 방송; 선택된게임명]` 형태의 헤더 구조로 완성해야 합니다.\n\n"
        "🚨 [출력 형식 및 제한 규칙]\n"
        "- 인사말, 설명, 주석, 분석 과정 등 모든 부연 설명을 절대 출력하지 마십시오.\n"
        "- 오직 매칭 결과가 반영된 최종 헤더 구조 단 한 줄만 문자열로 반환하십시오.\n"
        "- 예시: [게임 방송; 디아블로4]\n\n"
)

    user_prompt = (
        f"===[치지직 스트리머 마스터 DB (참고 사전)]===\n{streamers_db_content}\n\n"
        f"===[교정 대상 타임라인 본문 텍스트 (마커 보존 필수)]===\n{intermediate_text}\n\n"
        "각 행의 맨 뒤에 붙은 `[LINE_XX]` 마커를 완벽하게 유지하면서, 본문 내용들을 압축 명사구 형태로 깔끔하게 교정해 주세요."
    )

    corrected_text = ""
    try:
        model_name = globals().get("GEMINI_MODEL", "gemini-3.5-flash")
        max_tokens = globals().get("MAX_OUTPUT_TOKENS", 8192)

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.05, 
                max_output_tokens=max_tokens,
                top_p=0.95,
            )
        )
        corrected_text = response.text.strip()
    except Exception as e:
        log(f"⚠️ [Gemini 검수 예외] 1차 전제본으로 대체합니다: {e}", level="WARNING", show=SHOW)
        corrected_text = intermediate_text

    log(f"{corrected_text}", level="INFO", show=False)

    valid_items = []
    for m_id, meta in line_meta_table.items():
        if not meta["is_clip"]:
            valid_items.append({
                "timestamp": meta["timestamp"],
                "weight": meta["weight"]
            })

    valid_items.sort(key=lambda x: x["weight"], reverse=True)
    allowed_hot_timestamps = set()
    total_valid_count = len(valid_items)
    
    if total_valid_count > 0:
        target_count = math.floor(total_valid_count * 0.19)
        if target_count == 0 and total_valid_count >= 5:
            target_count = 1
            
        for i in range(target_count):
            allowed_hot_timestamps.add(valid_items[i]["timestamp"])

    corrected_lines = corrected_text.split("\n")
    final_lines = []
    
    for line in corrected_lines:
        line_strip = line.strip()
        if not line_strip:
            continue
            
        if line_strip.startswith("[") and ";" in line_strip:
            if not line_strip.endswith("]"):
                line_strip += "]"
            final_lines.append(line_strip)
            continue
            
        marker_match = re.search(r"\[(LINE_\d+)\]", line_strip)
        if marker_match:
            marker_id = marker_match.group(1)
            
            if marker_id in line_meta_table:
                orig_ts = line_meta_table[marker_id]["timestamp"]
                orig_icon = line_meta_table[marker_id]["icon"]
                
                pure_content = line_strip[:marker_match.start()].strip()
                pure_content = re.sub(r"^[\]\s🔥🎬]+", "", pure_content).strip()
                pure_content = re.sub(r"[\]\s🔥🎬]+$", "", pure_content).strip()
                ts_bracket = f"{orig_ts}"
                
                if orig_icon == "🎬":
                    line_output = f"{ts_bracket} 🎬 {pure_content}"
                elif orig_ts in allowed_hot_timestamps:
                    line_output = f"{ts_bracket} 🔥 {pure_content}"
                else:
                    line_output = f"{ts_bracket} {pure_content}"
                    
                final_lines.append(line_output)
            else:
                final_lines.append(re.sub(r"\[LINE_\d+\]", "", line_strip).strip())
        else:
            final_lines.append(line_strip)
        
    return "\n".join(final_lines)