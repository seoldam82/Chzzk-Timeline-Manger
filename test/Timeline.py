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
from Chzzk_api import sanitize_chzzk_url

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
    group_large: str = Field(description="방송 상황의 대분류 (예: 저스트 채팅, 게임 방송)")
    topic: str = Field(description="현재 시간대의 구체적인 대화 주제 또는 진행 중인 게임 명칭")
    timestamp: str = Field(description="hh:mm:ss 형식의 시간 축 지점")
    wf: float = Field(description="시청자 화력을 기반으로 소수점 둘째 자리까지 정밀 계산된 재미 점수 (실수)")
    wi: float = Field(description="공동 순위 방지를 위해 소수점 셋째 자리까지 극도로 쪼개진 중요 점수 (실수)")
    data_type: str = Field(description="데이터 원천 분류 타입 키워드 (오직 'ai' 또는 'chat'만 허용)")
    content: str = Field(description="타임라인 본문 내용 (상황 요약 문구 또는 채택된 시청자 채팅 원문)")

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
    chunk_pattern = os.path.join(specific_palette_dir, "temp_chunk_%03d.mp4")
    chunk_length_sec = 3600
    
    for f in glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.mp4")):
        try: os.remove(f)
        except: pass

    log("✂️ [오디오 가속] 전체 스트림을 60분 단위 순차 연산 청크로 분할 중...", show=SHOW)
    cmd_split = [
        ffmpeg_bin,
        '-y',
        '-i', audio_path,
        '-c', 'copy',
        '-f', 'segment',
        '-segment_time', str(chunk_length_sec),
        '-reset_timestamps', '1',
        chunk_pattern
    ]
    subprocess.run(cmd_split, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    chunk_files = sorted(glob.glob(os.path.join(specific_palette_dir, "temp_chunk_*.mp4")))
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
        "당신은 치지직/인방 다시보기 로그를 분석하여 구조화된 타임라인 데이터를 추출하는 유능한 영상 전문 편집자입니다.\n"
        "제공된 데이터를 바탕으로 아래의 방송 상태 분류 및 청크 예외 규칙을 엄격히 준수하여 타임라인 객체 배열을 구축하십시오.\n"
        "\n"
        "🚨 [방송 행동 상태 판별 절대 규칙: 저스트 채팅 vs 실제 게임플레이]\n"
        "- 오디오 STT의 문맥 및 채팅 문맥을 둘 다 분석하여 현재 콘텐츠 상태를 정밀 판별하십시오.\n"
        "  1. [게임 방송]: STT 내에 실시간 조작을 암시하는 발언('저장하자', '어디로 가지?', '죽었다', '잡았다', 방향 및 조작 언급)이 감지되면 무조건 게임플레이 상태로 확정합니다.\n"
        "  2. [저스트 채팅]: 위 조작 트리거가 없으며 웹서핑, 영도 시청, 과거 플레이 회상 및 썰 풀기를 진행하는 온전한 토크 위주의 상태입니다.\n"
        "- 정확한 게임명을 적기 위해 노력하되 명칭을 유추할 수 없다면, 채팅과 STT 문맥상의 장르를 바탕으로 명사 형태로 작성하십시오. (예: 사과 게임, 은신 게임)\n"
        "\n"
        "🚨 [청크 처리 규칙]\n"
        "- 현재 데이터는 전체 영상이 일정 단위로 분할(Chunk)되어 입력되는 상태입니다. 단순히 청크의 시작이나 끝이라는 이유만으로 '방송 시작', '방종 준비', '마무리 토크' 등으로 내용을 성급하게 유추하는 것을 엄격히 금지합니다.\n"
        "- [방송 시작 조건]: 스트리머의 실제 명확한 오프닝 인사 및 방종 인사(streamer_info 참고)가 검증될 때만 시작/방종 소주제를 허용합니다. 단순 게임 시작 및 종료는 방송 시작/종료를 의미하지 않습니다.\n"
        "\n"
        "🚨 [소주제(topic) 작성 제약 규칙]\n"
        "- 소주제 명칭에 합방 멤버, 디스코드 대화 참여자 등 타 스트리머의 닉네임이나 이름, 괄호 표기를 절대로 포함하지 마십시오. 오직 순수한 콘텐츠 명칭이나 게임 제목, 대화 주제만 깔끔하게 작성하십시오.\n"
        "- 스트리머와 채팅을 바탕으로 동일한 게임을 계속하고 있다고 유추된다면, 세부 상황이 바뀌더라도 소주제 명칭은 최초에 지정한 명칭으로 완벽하게 동일하게 유지해야 합니다.\n"
        "- 단어 맨 뒤에 '플레이', '방송', '진행', '하기'와 같은 불필요한 서술성 명사나 접미사를 절대 붙이지 마십시오.\n"
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
                    file_content = jf.read()
                
                item_pattern = re.compile(
                    r'\{\s*"group_large"\s*:\s*"(.*?)",\s*'
                    r'"topic"\s*:\s*"(.*?)",\s*'
                    r'"timestamp"\s*:\s*"(.*?)",\s*'
                    r'"wf"\s*:\s*([\d\.]+),\s*'
                    r'"wi"\s*:\s*([\d\.]+),\s*'
                    r'"data_type"\s*:\s*"(.*?)",\s*'
                    r'"content"\s*:\s*"(.*?)"\s*\}', 
                    re.DOTALL
                )
                
                matches = item_pattern.findall(file_content)
                
                for match in matches:
                    group_large, topic, ts_str, wf_str, wi_str, data_type, content = match
                    data_type = data_type.strip().lower()
                    content = content.strip()
                    ts_str = ts_str.strip().replace("[", "").replace("]", "")
                    
                    if data_type == "chat":
                        clean_text = content.replace("🔥", "").replace(" ", "").strip()
                        if len(clean_text) < 2:
                            continue
                    
                    time_match = re.match(r"^(\d{2}):(\d{2}):(\d{2})", ts_str)
                    if time_match:
                        h, m, s = map(int, time_match.groups())
                        sec_val = h * 3600 + m * 60 + s
                        
                        wf = float(wf_str)
                        wi = float(wi_str)
                        total_score = float(f"{wf + wi:.3f}")
                        
                        weight_sec_map[sec_val] = {
                            "original_ts": ts_str,
                            "total_weight": total_score,
                            "topic": topic,
                            "data_type": data_type,
                            "content": content
                        }
                        
    except Exception as j_err:
        print(f"❌ JSON 데이터 로딩 및 정규식 파싱 중 오류 발생: {j_err}")

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
            matched_json_data = {"original_ts": f"{h}:{m}:{s}", "total_weight": 0.000, "topic": "", "data_type": "", "content": ""}
            min_delta = float('inf')
            
            for j_sec, j_data in weight_sec_map.items():
                delta = abs(j_sec - txt_sec)
                if delta < min_delta:
                    min_delta = delta
                    matched_json_data = j_data
            
            j_type = matched_json_data.get("data_type", "")
            j_content = matched_json_data.get("content", "")
            
            if j_type == "chat" or "🔥" in line_strip:
                clean_text = j_content.replace("🔥", "").replace(" ", "").strip()
                if not clean_text:
                    clean_text = line_strip[match_ts.end():].replace("🔥", "").replace("🎬", "").replace(" ", "").strip()
                
                if len(clean_text) < 2:
                    continue
            
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
        "당신은 인터넷 방송 다시보기 타임라인 본문의 맞춤법과 어색한 문장 구조를 종합 검수하는 최상위 전문 편집자입니다.\n"
        "\n"
        "🚨 [절대 규칙: 고유 마커 마크 고정 및 유실 금지]\n"
        "- 문장의 맨 뒤에 붙어 있는 `[LINE_0]`, `[LINE_1]` 형태의 고유 마커 꼬리표는 매칭 시스템의 인덱스 키이므로 **절대 삭제, 수정, 변경, 누락하거나 자리를 바꾸지 마십시오.**\n"
        "- 필터링에서 살아남은 라인들은 문장 끝부분의 `[LINE_XX]` 포맷을 완벽하게 그대로 유지한 채 출력해야 합니다.\n"
        "\n"
        "🔥 [최우선 필터링 조항: 욕설 채팅 무조건 즉각 삭제]\n"
        " **[욕설 및 비속어 채팅 제거]**:\n"
        "   - 본문 내용에 '시발', '존나', '지랄', '새끼', '뻐큐' 등 직간접적인 모든 욕설, 비속어, 비하 표현이 단 한 글자라도 포함되어 있다면 점수와 관계없이 **그 라인 전체를 통째로 삭제(누락) 처리**하십시오.\n"
        "\n"
        "🔥 [최우선 적용: 채팅 데이터 욕설 및 글자수 강력 필터링 절대 규칙]\n"
        "1. **[채팅 내 욕설/비속어 포함 라인 영구 제거]**:\n"
        "   - 시청자 채팅 원문이 채택된 라인 중 '지랄', '시발', '존나', '새끼', '뻐큐' 등 직간접적인 모든 욕설, 비속어, 비하 표현이 단 한 글자라도 포함되어 있다면, 해당 라인은 검수하지 말고 **통째로 완전 누락(삭제) 처리**하여 최종 출력에서 배제하십시오.\n"
        "2. **[2글자 미만 단발성 채팅 라인 영구 제거]**:\n"
        "   - 'ㅋ', 'ㄱ', '?', 'ㅎ', 'ㅠ', 'ㄷ' 등 공백과 심볼(🔥)을 제외한 순수 글자 수가 2글자 미만(1글자 이하)인 모든 단발성 리액션 채팅 라인은 예외 없이 **통째로 완전 누락(삭제) 처리**하여 절대 출력하지 마십시오. (예: '🔥 ㅋㅋㅋ'는 3글자이므로 유지, '🔥 ㄱ-' 또는 '🔥 ㅋ'는 2글자 미만이므로 무조건 삭제)\n"
        "\n"
        "🚨 [핵심 임무: 본문 압축 및 헤더 보존 규칙]\n"
        "- 타임라인 중간중간 삽입되어 있는 모든 `[대주제; 소주제]` 형태의 단락 헤더 구조를 절대 임의로 통합하거나 삭제하지 말고, 원래 위치 그대로 완벽히 보존하십시오.\n"
        "- 필터링을 통과한 유효한 타임라인 본문 설명 문장은 권장 15~25자 내외의 생동감 있는 명사형으로 간결하게 정제하십시오. (~함, ~하는 모습 등 금지)\n"
        "- 합방 멤버나 스트리머의 불필요한 닉네임, 중복 수식어를 지우고 가독성을 극대화하십시오.\n"
        "\n"
        "🚨 [🚨 절대 금지: 극단적 축약 및 무맥락 요약 금지]\n"
        "- 상황 파악이 불가능한 6자 미만의 극단적인 무맥락 요약(예: '독극물', '차이점', '공포', '의문', '반응')은 절대 금지합니다.\n"
        "- 짧게 정제하더라도 '구체적인 고유명사(대상, 아이템, 게임 상황)'와 '스트리머의 구체적인 행동/감정 상태'가 식별되도록 본문의 핵심 맥락을 반드시 살려두십시오.\n"
        "\n"
        "🚨 [유효 시청자 채팅 원문 보존 규칙]\n"
        "- 위의 욕설 필터링과 2글자 미만 필터링을 완벽하게 통과한 정상적인 시청자 채팅 원문 라인은 맞춤법 교정이나 명사형 종결 규칙을 적용하지 말고, 입력된 원문 텍스트 문맥 상태를 그대로 보존하여 출력하십시오.\n"
        "\n"
        "🚨 [금지어 세부 규칙]\n"
        "1. 문장 내에 '언급', '언급함' 이라는 단어는 절대 금지합니다. 문맥에 따라 '이야기', '토크', '소통' 등으로 완벽하게 우회하십시오.\n"
        "2. 문장 내에 '자살', '자해'라는 단어는 단 한 번도 절대 사용하지 마십시오.\n"
        "3. 마크다운 코드 블록 마크(```)는 절대 포함하지 말고 순수 텍스트만 출력하십시오.\n"
        "4. 욕설을 포함하고 있다면 삭제 후 출력하십시오.\n"
        "\n"
        "🚨 [단락별 헤더 카테고리 매칭 규칙]\n"
        "- 텍스트 내에서 `[게임 방송; ...]` 형태의 헤더를 발견하면, 해당 소주제 텍스트의 핵심 맥락을 파악하여 아래 후보 리스트와 매칭 작업을 수행하십시오.\n"
        f"- [제공된 후보 리스트]: {game_category}\n"
        "- **[최우선 사항]** 헤더의 게임 명칭을 결정할 때 임의로 지어내지 말고, **반드시 위에 제공된 후보 리스트 안에 존재하는 게임 명칭을 최우선적으로 매칭하여 교정**하십시오.\n"
        "\n"
        "🚨 [출력 형식 및 제한 규칙]\n"
        "- 인사말, 설명, 주석, 분석 과정 등 모든 부연 설명을 절대 출력하지 마십시오.\n"
        "- 필터링(욕설 제거 및 2글자 미만 제거)과 본문 검수가 완벽하게 끝난 최종 타임라인 결과물만 처음부터 끝까지 빈 줄 구조를 유지하여 반환하십시오.\n"
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