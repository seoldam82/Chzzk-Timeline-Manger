import os
import sys
import math
import signal
import re
import glob
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from Chzzk_api import (
    select_chzzk_vod, 
    download_chzzk_vod_chats, 
    write_chzzk_comment, 
    CONFIG
)
from Timeline import (
    download_chzzk_vod_audio,
    transcribe_chzzk_audio,
    generate_chzzk_timeline,
    merge_and_format_final_timeline,
    timestamp_to_seconds,
    Final_Processing,
)
from clip import (
    get_clips_by_specific_vod,
    fetch_vod_detailed_info,
    convert_seconds_to_hms
)

def parse_chat_timestamp_to_secs(chat_line):
    match = re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\]", chat_line)
    if match:
        h, m, s = map(int, match.groups())
        return h * 3600 + m * 60 + s
    
    match_short = re.match(r"^\[(\d{2}):(\d{2})\]", chat_line)
    if match_short:
        m, s = map(int, match_short.groups())
        return m * 60 + s
        
    return None

def analyze_chat_importance_is_video(chat_data_str):
    if not chat_data_str.strip():
        return False
    video_keywords = re.compile(r'(ㅋ{2,}|ㄱㅇㅇ|\?{2,}|와|이게|대박|미쳤|❗|❓|😮|🔥|👏|👍)')
    lines = chat_data_str.split('\n')
    video_hits = 0
    valid_lines = 0
    for line in lines:
        if not line.strip(): continue
        valid_lines += 1
        msg_parts = line.split(']', 2)
        content = msg_parts[-1] if len(msg_parts) > 1 else line
        if video_keywords.search(content):
            video_hits += 1
    if valid_lines == 0: return False
    return (video_hits / valid_lines) >= 0.45

def find_nearest_stt_start_seconds(target_sec, script_lines):
    nearest_sec = target_sec
    min_diff = float('inf')
    for line in script_lines:
        line_strip = line.strip()
        if not line_strip: continue
        match = re.match(r"^\[(\d+:\d+:\d+)\]", line_strip)
        if match:
            line_secs = timestamp_to_seconds(match.group(1))
            diff = abs(line_secs - target_sec)
            if diff < min_diff and diff <= 15:
                min_diff = diff
                nearest_sec = line_secs
    return nearest_sec

def process_direct_comment_mode():
    print("\n-------------------------------------------------------------------------")
    print("📂 기존 타임라인 파일 불러오기 및 바로 댓글 등록 모드")
    print("-------------------------------------------------------------------------")
    
    search_pattern = os.path.join("TL_VOD", "*", "TL_VOD_*.txt")
    txt_files = glob.glob(search_pattern)
    
    if not txt_files:
        print("❌ 'TL_VOD/(VOD번호)/' 디렉토리에 생성된 텍스트 파일이 없습니다.")
        print("💡 모드 1을 선택해 타임라인을 먼저 새로 생성해 주세요.")
        return

    print("📝 등록 가능한 로컬 타임라인 파일 목록:")
    for idx, filepath in enumerate(txt_files, 1):
        print(f" [{idx}] {filepath}")
        
    try:
        choice = int(input("\n👉 업로드할 파일 번호를 선택하세요: ").strip())
        if choice < 1 or choice > len(txt_files):
            print("❌ 잘못된 번호입니다. 프로그램을 종료합니다.")
            return
        selected_file = txt_files[choice - 1]
    except ValueError:
        print("❌ 숫자로 입력해 주세요. 프로그램을 종료합니다.")
        return

    vod_id_match = re.search(r"TL_VOD_(\d+)_", os.path.basename(selected_file))
    if vod_id_match:
        vod_id = vod_id_match.group(1)
    else:
        vod_id = input("🔍 파일명에서 VOD ID를 자동 추출하지 못했습니다. 대상 VOD 일련번호를 직접 입력하세요: ").strip()

    try:
        with open(selected_file, "r", encoding="utf-8") as f:
            comment_content = f.read()
    except Exception as e:
        print(f"❌ 파일을 읽는 중 오류 발생: {e}")
        return

    print("\n=========================================================================")
    print(f"📖 선택한 파일 [{selected_file}] 내용 미리보기")
    print("=========================================================================")
    print(comment_content[:1000]) 
    if len(comment_content) > 1000:
        print("\n... (이하 생략) ...")
    print("=========================================================================")
    
    timeline_len = len(comment_content)
    print(f"📊 현재 로드된 타임라인 글자 수: {timeline_len}자 / 5000자")

    if timeline_len > 5000:
        print("⚠️ [경고] 타임라인 총 길이가 5000자를 초과하여 치지직 댓글 제한에 걸립니다.")
        print("💡 텍스트 파일을 직접 열어 내용을 5000자 이내로 줄여주신 다음 다시 시도해 주세요.")
        return

    answer = input(f"\n🚀 이 타임라인을 VOD [{vod_id}]번에 즉시 반영할까요? (y/n): ").strip().lower()
    if answer == 'y':
        write_chzzk_comment(video_no=vod_id, comment_text=comment_content)
    else:
        print("👋 작업을 취소했습니다.")


def run_pure_test():
    print("\n-------------------------------------------------------------------------")
    print("🤖 새 VOD 타임라인 생성 및 추출 모드 시작")
    print("-------------------------------------------------------------------------")
    
    TARGET_CHANNEL_ID = CONFIG.get("TARGET_CHANNEL_ID")
    GEMINI_API_KEY = CONFIG.get("GEMINI_API_KEY")
    WHISPER_MODEL = CONFIG.get("WHISPER_MODEL", "base")
    
    if not GEMINI_API_KEY or GEMINI_API_KEY.strip() == "":
        print("❌ Gemini API 키가 설정되지 않았습니다. config.json 파일의 'GEMINI_API_KEY' 항목을 확인하세요.")
        return
    
    voicepalette_BASE_DIR = "./voicepalette"
    if not os.path.exists(voicepalette_BASE_DIR):
        os.makedirs(voicepalette_BASE_DIR)

    try:
        vod_limit_input = input("➡️  불러올 VOD 개수를 입력하세요 (기본값: 10): ").strip()
        vod_limit = int(vod_limit_input) if vod_limit_input else 10
        if vod_limit <= 0:
            vod_limit = 10
    except ValueError:
        print("❌ 올바른 숫자가 아닙니다. 기본값인 10개로 탐색을 시작합니다.")
        vod_limit = 10

    vod_result = select_chzzk_vod(TARGET_CHANNEL_ID, limit=vod_limit)
    
    if vod_result is None:
        print("❌ 유효한 치지직 VOD 일련번호를 획득하지 못했습니다.")
        return

    vod_id = None
    actual_title = "방송다시보기"
    video_duration = 0

    if isinstance(vod_result, (tuple, list)):
        if len(vod_result) > 0 and vod_result[0]:
            vod_id = str(vod_result[0]).strip()
        if len(vod_result) > 1 and vod_result[1]:
            actual_title = str(vod_result[1]).strip()
        if len(vod_result) > 2 and vod_result[2]:
            try:
                video_duration = int(vod_result[2])
            except ValueError:
                video_duration = 0
    else:
        vod_id = str(vod_result).strip()

    if not vod_id or vod_id.lower() == "none" or vod_id == "0":
        print("❌ 선택된 VOD ID가 올바르지 않습니다. 채널 ID 혹은 config 세션을 재점검하세요.")
        return

    print(f"🔍 [클립 동기화 엔진] VOD [{vod_id}]의 원천 상세 날짜를 취득하는 중...")
    vod_info = fetch_vod_detailed_info(vod_id)
    if not vod_info:
        print("❌ VOD의 시작 날짜 및 세부 시간 레이어를 취득하지 못했습니다. 클립 동기화를 진행할 수 없습니다.")
        return

    full_vod_url = f"https://chzzk.naver.com/video/{vod_id}"  
    print(f"\n🎬 선택된 타겟 방송: [{actual_title}] (VOD ID: {vod_id})")
    
    try:
        start_percent = float(input("➡️  전체 분석 시작 시간 백분율(%)을 입력하세요 (예: 0 -> 처음부터): ").strip())
        end_percent = float(input("➡️  전체 분석 종료 시간 백분율(%)을 입력하세요 (예: 100 -> 끝까지): ").strip())
    except ValueError:
        print("❌ 올바른 숫자를 입력하세요. 기본값(0% ~ 100%)으로 매핑하여 시작합니다.")
        start_percent = 0.0
        end_percent = 100.0

    total_duration_secs = video_duration if video_duration > 0 else (vod_info.get("duration", 0) if vod_info.get("duration", 0) > 0 else 14400)
    global_start_sec = int(total_duration_secs * (start_percent / 100.0))
    global_end_sec = int(total_duration_secs * (end_percent / 100.0))

    print(f"⏱️ 영상 총 환산 시간: 약 {total_duration_secs}초")
    print(f"🎯 전체 분석 타겟 구간: {global_start_sec}초 ~ {global_end_sec}초 범위")

    master_audio_path = download_chzzk_vod_audio(chzzk_url=full_vod_url, vod_id=vod_id)
    if not master_audio_path or not os.path.exists(master_audio_path):
        print("❌ 전체 오디오 캐시 데이터 생성 과정에 실패했습니다.")
        return

    full_script_path = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}", "full_raw_script.txt")
    full_transcription = transcribe_chzzk_audio(
        audio_path=master_audio_path,
        target_path=full_script_path,
        model_size=WHISPER_MODEL  
    )
    if not full_transcription.strip():
        print("❌ VOD 전체 대본(STT) 데이터가 유효하지 않거나 비어있습니다.")
        return

    script_lines = full_transcription.split("\n")

    print("\n🎬 클립 데이터 기반 동적 AI 가산점 레이어 설계 중...")
    all_clips = get_clips_by_specific_vod(TARGET_CHANNEL_ID, vod_id, pre_fetched_info=vod_info)
    popular_clips = []
    clip_score_mod_guide = []
    
    from clip import get_clip_density_context_string
    clip_density_context = get_clip_density_context_string(all_clips)
    
    if all_clips:
        from datetime import datetime
        import datetime as dt_module
        
        def extract_date_obj(raw_val):
            if not raw_val:
                return None
            
            if isinstance(raw_val, (datetime, dt_module.date)):
                return datetime(raw_val.year, raw_val.month, raw_val.day)
            
            if isinstance(raw_val, dict):
                raw_val = (
                    raw_val.get("createdDate") or 
                    raw_val.get("createDate") or 
                    raw_val.get("createdAt") or 
                    raw_val.get("publishDate") or ""
                )

            val_str = str(raw_val).strip()
            if not val_str or val_str.lower() == "none":
                return None

            if "T" in val_str:
                val_str = val_str.split("T")[0]
            elif " " in val_str:
                val_str = val_str.split(" ")[0]

            try:
                if "-" in val_str:
                    return datetime.strptime(val_str, "%Y-%m-%d")
                elif "." in val_str:
                    return datetime.strptime(val_str, "%Y.%m.%d")
                elif "/" in val_str:
                    return datetime.strptime(val_str, "%Y/%m/%d")
            except ValueError:
                pass

            match_tuple = re.search(r"datetime\s*\.\s*datetime\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})", val_str)
            if match_tuple:
                try:
                    return datetime(int(match_tuple.group(1)), int(match_tuple.group(2)), int(match_tuple.group(3)))
                except:
                    pass
            
            match_comma = re.search(r"(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})", val_str)
            if match_comma:
                try:
                    return datetime(int(match_comma.group(1)), int(match_comma.group(2)), int(match_comma.group(3)))
                except:
                    pass
            return None

        vod_publish_date_raw = (
            vod_info.get("publishDate") or 
            vod_info.get("createDate") or 
            vod_info.get("registrationDate") or 
            vod_info.get("openDate") or 
            vod_info.get("start_time") or ""
        )
        
        today_date = datetime.now()
        today_date = datetime(today_date.year, today_date.month, today_date.day)
        
        vod_date = extract_date_obj(vod_publish_date_raw)
        recent_clip_count = 0
        valid_date_clips = 0
        unpopular_clip_count = 0 
        
        for clip in all_clips:
            clip_create_raw = clip.get("createdDate")
            clip_date = extract_date_obj(clip_create_raw)
            
            if not clip_date:
                clip_date = extract_date_obj(clip)
            
            if not clip_date or not vod_date:
                continue
                
            valid_date_clips += 1
            days_diff = abs((today_date - clip_date).days)
            
            if days_diff <= 7:
                recent_clip_count += 1

        disable_low_view_filter = False
        denominator = valid_date_clips if valid_date_clips > 0 else len(all_clips)
        recent_ratio = recent_clip_count / denominator if denominator > 0 else 0
        
        print("-------------------------------------------------------------------------")
        print(f"📊 [클립 데이터 현황 통계]")
        print(f"   - 수집된 전체 클립 개수: {len(all_clips)}개")
        if vod_date:
            print(f"   - 식별된 VOD 등록 날짜: {vod_date.strftime('%Y-%m-%d')}")
            print(f"   - 일주일(7일) 이내 생성된 최신 클립: {recent_clip_count}개 / (분석 가능: {valid_date_clips}개)")
        print("-------------------------------------------------------------------------")

        if (recent_ratio >= 0.5) or (valid_date_clips > 0 and recent_clip_count == valid_date_clips):
            disable_low_view_filter = True
            print(f"⏱️   [최신 VOD 감지 성공] 최신 클립 비율이 {recent_ratio*100:.1f}%로 과반수를 충족합니다.")
            print("🔓 [필터 비활성화] 최신 방송 세션이므로 '조회수 100회 이하 배제 규칙'을 비활성화합니다.")
        else:
            print(f"📦 [일반 VOD 분석] 일주일 이내 생성 클립 비율({recent_ratio*100:.1f}%)")
            print("🔒 [필터 활성화] 최신 방송 세션이 아니므로 '조회수 100회 이하 배제 규칙'을 활성화합니다.")

        all_clips.sort(key=lambda x: x.get("viewCount", x.get("readCount", 0)), reverse=True)
        
        cutoff_passed_clips = []
        unpopular_clip_count = 0

        for clip in all_clips:
            views = clip.get("viewCount", clip.get("readCount", 0))
            if (not disable_low_view_filter) and (views <= 100):
                unpopular_clip_count += 1
            else:
                cutoff_passed_clips.append(clip)

        unique_clips = []
        removed_clip_count = 0

        for clip in cutoff_passed_clips:
            pos_secs = clip.get("realPlaybackPosition", 0)
            is_duplicate = False
            for u_clip in unique_clips:
                if abs(u_clip.get("realPlaybackPosition", 0) - pos_secs) < 180:
                    is_duplicate = True
                    removed_clip_count += 1
                    break
            if not is_duplicate:
                unique_clips.append(clip)

        total_unique_count = len(unique_clips)
        
        if total_unique_count < 10:
            top_cutoff_limit = 3
            print(f"⚠️   [소형 클립 세션] 최종 클립이 {total_unique_count}개이므로 3개를 추출합니다.")
        else:
            top_cutoff_limit = max(1, math.ceil(total_unique_count * 0.3))
            print(f"📊 [정상 세션] 최종 클립이 {total_unique_count}개이므로 상위 30%(반올림)인 {top_cutoff_limit}개를 추출합니다.")
        
        final_popular_clips = []
        
        for idx, clip in enumerate(unique_clips):
            views = clip.get("viewCount", clip.get("readCount", 0))
            pos_secs = clip.get("realPlaybackPosition", 0)
            hms = convert_seconds_to_hms(pos_secs)
            
            if views >= 1000:
                clip_score_mod_guide.append(f"- [{hms} 근처]: 조회수 1,000회 이상 대형 클립 구역 (재미 wf, 중요 wi 가산 및 무조건 배치)")
                final_popular_clips.append(clip)
            elif idx < top_cutoff_limit:
                clip_score_mod_guide.append(f"- [{hms} 근처]: 상위 인기 클립 구역 (재미 wf, 중요 wi 가산)")
                final_popular_clips.append(clip)
            else:
                unpopular_clip_count += 1

        popular_clips = final_popular_clips
        
        print("-------------------------------------------------------------------------")
        print(f"✅ [클립 최종 스크리닝 완료]")
        print(f"   - 최종 채택 클립: {len(popular_clips)}개")
        print(f"   - 중복 제거 클립: {removed_clip_count}개")
        print(f"   - 순위 탈락 혹은 기준 미달 클립: {unpopular_clip_count}개")
        print("=========================================================================")

    clip_guide_text = "\n".join(clip_score_mod_guide) if clip_score_mod_guide else "없음 (기본 가중치 적용)"

    CHUNK_SIZE_SECS = 3600
    all_raw_items = []
    
    current_chunk_start = global_start_sec
    while current_chunk_start < global_end_sec:
        current_chunk_end = min(current_chunk_start + CHUNK_SIZE_SECS, global_end_sec)
        chunk_index = int(current_chunk_start // CHUNK_SIZE_SECS)
        
        print(f"\n🔄 [청크 슬라이싱] {current_chunk_start}초 ~ {current_chunk_end}초 구간 텍스트 추출 중... (인덱스: {chunk_index})")

        compressed_chat_data = download_chzzk_vod_chats(vod_id, current_chunk_start, current_chunk_end)
        chunk_script_lines = []
        for line in script_lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            match = re.match(r"^\[(\d+:\d+:\d+)\]", line_strip)
            if match:
                line_secs = timestamp_to_seconds(match.group(1))
                if current_chunk_start <= line_secs < current_chunk_end:
                    chunk_script_lines.append(line_strip)

        chunk_transcription_text = "\n".join(chunk_script_lines)

        if not chunk_transcription_text.strip():
            print(f"⚠️ [{chunk_index}번 청크] 해당 시간 구간에 매칭되는 STT 텍스트 대본이 없습니다. 건너뜁니다.")
            current_chunk_start = current_chunk_end
            continue

        try:
            with open(os.path.join(os.getcwd(), "voicepalette", "last_raw_script.txt"), "w", encoding="utf-8") as lf:
                lf.write(chunk_transcription_text)
        except:
            pass

        enhanced_input_script = (
            f"==== [🚨 타임라인 가산점 및 클립 밀도 절대 규칙] ====\n"
            f"1. 개별 인기 클립 가이드:\n"
            f"{clip_guide_text}\n\n"
            f"2. [핵심] 10분 단위 클립 생성 밀집 구간 (대량 핫스팟 구역):\n"
            f"{clip_density_context}\n\n"
            f"위 밀집 구간 내부의 대화나 리액션은 유저 피드백이 폭발한 지점이므로,\n"
            f"해당 시간대의 타임라인 항목은 누락 없이 촘촘하고 상세하게 추출하도록 채점 가중치(wf, wi)를 극대화하십시오.\n"
            f"====================================================\n\n"
            f"{chunk_transcription_text}"
        )

        print(f"🚀 Gemini AI 원격 모델 호출 중 (청크 인덱스: {chunk_index})...")
        chunk_items = generate_chzzk_timeline(
            input_script=enhanced_input_script,
            chat_script=compressed_chat_data,
            actual_title=actual_title,
            chzzk_url=full_vod_url,
            api_key=GEMINI_API_KEY,
            chunk_index=chunk_index
        )

        if chunk_items:
            all_raw_items.extend(chunk_items)
            
        current_chunk_start = current_chunk_end

    if not all_raw_items:
        print("❌ Gemini AI가 정상적인 타임라인 항목 뼈대를 생성하지 못했습니다.")
        return

    final_output_text = merge_and_format_final_timeline(all_raw_items)

    print("⚙️  [최종 후처리] Gemini 모델을 활용한 문맥/유사도 기반 닉네임 교정 작업 수행 중...")
    final_output_text = Final_Processing(
        timeline_text=final_output_text, 
        api_key=GEMINI_API_KEY, 
        db_filename="chzzk_streamers.txt"
    )

    ai_notice = "🤖 이 댓글은 방송 하이라이트를 AI가 분석하여 생성한 타임라인으로 다소 부정확한 부분이 있을 수 있습니다."
    new_header = f"00:00:00 {actual_title}"
    lines = final_output_text.split("\n")
    
    timeline_stream_items = []
    current_header = "[저스트 채팅; 방송 잡담 및 일상 공유]"
    
    def parse_time_to_seconds(ts_str):
        time_match = re.search(r"(\d{2}):(\d{2}):(\d{2})", ts_str)
        if time_match:
            h, m, s = map(int, time_match.groups())
            return h * 3600 + m * 60 + s
        return 999999

    for line in lines:
        line_strip = line.strip()
        
        if "🤖 이 댓글은" in line_strip or line_strip.startswith("00:00:00") or not line_strip:
            continue
            
        if line_strip.startswith("[") and ";" in line_strip and line_strip.endswith("]"):
            current_header = line_strip
        else:
            secs = parse_time_to_seconds(line_strip)
            timeline_stream_items.append({
                "seconds": secs,
                "header": current_header,
                "text": line_strip,
                "is_clip": False
            })

    if popular_clips:
        def normalize_sub_topic_to_set(header_str):
            if ";" not in header_str:
                return set()
            sub_topic = header_str.split(";")[1].replace("]", "").strip()
            sub_topic = re.sub(r"(게임|플레이|방송|시뮬레이터|튜토리얼|\s)", "", sub_topic)
            return set(sub_topic) if sub_topic else set()

        for clip in popular_clips:
            pos_secs = clip.get("realPlaybackPosition", 0)
            duration = clip.get("clipDuration", 0)
            
            clip_chat_data = download_chzzk_vod_chats(vod_id, pos_secs, pos_secs + duration)
            is_video_centric = analyze_chat_importance_is_video(clip_chat_data)
            
            if is_video_centric:
                final_timestamp_sec = pos_secs
            else:
                final_timestamp_sec = find_nearest_stt_start_seconds(pos_secs, script_lines)
                
            timestamp_str = convert_seconds_to_hms(final_timestamp_sec)
            c_title = clip.get("clipTitle", "제목 없음").strip()
            formatted_clip_line = f"{timestamp_str} 🎬 {c_title}"
            matched_header = "[저스트 채팅; 방송 잡담 및 일상 공유]"
            min_diff = float('inf')
            closest_item_idx = -1
            
            for idx, item in enumerate(timeline_stream_items):
                diff = abs(item["seconds"] - final_timestamp_sec)
                if diff < min_diff:
                    min_diff = diff
                    matched_header = item["header"]
                    closest_item_idx = idx

            category_type = clip.get("categoryType", "")
            clip_category_name = clip.get("clipCategory", "").strip()
            
            if category_type == "GAME" and clip_category_name:
                new_game_header = f"[게임 방송; {clip_category_name}]"
                target_old_header = matched_header
                
                if "게임 방송" in target_old_header:
                    target_set = normalize_sub_topic_to_set(target_old_header)
                    matched_header = new_game_header
                    
                    for item in timeline_stream_items:
                        if "게임 방송" in item["header"]:
                            if item["header"] == target_old_header:
                                item["header"] = new_game_header
                            else:
                                current_set = normalize_sub_topic_to_set(item["header"])
                                if target_set and current_set:
                                    intersection = target_set.intersection(current_set)
                                    union = target_set.union(current_set)
                                    similarity = len(intersection) / len(union) if union else 0
                                    if similarity >= 0.35:
                                        item["header"] = new_game_header
                                
                    if closest_item_idx != -1:
                        left_idx = closest_item_idx
                        while left_idx >= 0 and "게임 방송" in timeline_stream_items[left_idx]["header"]:
                            curr_h = timeline_stream_items[left_idx]["header"]
                            curr_set = normalize_sub_topic_to_set(curr_h)
                            
                            is_contig = False
                            if curr_h == target_old_header:
                                is_contig = True
                            elif target_set and curr_set:
                                inter = target_set.intersection(curr_set)
                                uni = target_set.union(curr_set)
                                if (len(inter) / len(uni) if uni else 0) >= 0.35:
                                    is_contig = True
                                    
                            if is_contig:
                                timeline_stream_items[left_idx]["header"] = new_game_header
                            left_idx -= 1
                            
                        right_idx = closest_item_idx + 1
                        while right_idx < len(timeline_stream_items) and "게임 방송" in timeline_stream_items[right_idx]["header"]:
                            curr_h = timeline_stream_items[right_idx]["header"]
                            curr_set = normalize_sub_topic_to_set(curr_h)
                            
                            is_contig = False
                            if curr_h == target_old_header:
                                is_contig = True
                            elif target_set and curr_set:
                                inter = target_set.intersection(curr_set)
                                uni = target_set.union(curr_set)
                                if (len(inter) / len(uni) if uni else 0) >= 0.35:
                                    is_contig = True
                                    
                            if is_contig:
                                timeline_stream_items[right_idx]["header"] = new_game_header
                            right_idx += 1
                else:
                    matched_header = new_game_header
            
            timeline_stream_items.append({
                "seconds": final_timestamp_sec,
                "header": matched_header,
                "text": formatted_clip_line,
                "is_clip": True
            })

    timeline_stream_items.sort(key=lambda x: (x["seconds"], not x["is_clip"]))

    filtered_stream = []
    
    for item in timeline_stream_items:
        keep = True
        
        for standard in filtered_stream:
            if abs(standard["seconds"] - item["seconds"]) < 180:
                if not standard["is_clip"] and item["is_clip"]:
                    filtered_stream.remove(standard)
                    break
                elif standard["is_clip"] and not item["is_clip"]:
                    keep = False
                    break
                else:
                    keep = False
                    break
                    
        if keep:
            filtered_stream.append(item)

    filtered_stream.sort(key=lambda x: x["seconds"])

    cleaned_body_list = []
    active_header = None
    seen_entries = set()

    for item in filtered_stream:
        entry_text = item["text"].strip()
        
        if entry_text in seen_entries:
            continue
        
        seen_entries.add(entry_text)
        
        if item["header"] != active_header:
            if active_header is not None:
                cleaned_body_list.append("")
            active_header = item["header"]
            cleaned_body_list.append(active_header)
            
        cleaned_body_list.append(entry_text)

    final_timeline_string = "\n".join([ai_notice, new_header, ""] + cleaned_body_list).strip()
    
    target_dir = os.path.join("TL_VOD", str(vod_id))
    file_name = f"TL_VOD_{vod_id}_{int(start_percent)}_{int(end_percent)}.txt"
    output_path = os.path.join(target_dir, file_name)

    os.makedirs(target_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_timeline_string)

    print("\n=========================================================================")
    print("🎯 [완성] 최종 통합본")
    print("=========================================================================")
    print(final_timeline_string)
    print("=========================================================================")
    print(f"💾 최종 타임라인 결과 파일이 '{output_path}'로 안전하게 출력되었습니다!")

    timeline_len = len(final_timeline_string)
    print(f"\n📊 현재 생성된 타임라인 글자 수: {timeline_len}자 / 5000자")

    if timeline_len > 5000:
        print("⚠️ [경고] 타임라인 총 길이가 5000자를 초과하여 치지직 댓글 시스템에 등록할 수 없습니다.")
        print("💡 해결책: 분석할 VOD 범위를 조금 더 좁게 나누어 청크 처리를 시도하세요.")
    else:
        answer = input(f"\n🚀 이 타임라인을 VOD [{vod_id}]번에 즉시 반영(등록/수정)할까요? (y/n): ").strip().lower()
        if answer == 'y':
            write_chzzk_comment(video_no=vod_id, comment_text=final_timeline_string)
        else:
            print("👋 댓글 작성을 취소했습니다. 로컬 텍스트 파일만 보존됩니다.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    
    print("=========================================================================")
    print("             치지직 VOD 타임라인 매니저              ")
    print("=========================================================================")
    print(" [1] AI 연산 실행하여 새 타임라인 파일 생성하기")
    print(" [2] 이미 만들어진 로컬 타임라인 파일 불러와 즉시 댓글 등록하기")
    print("-------------------------------------------------------------------------")
    
    menu = input("👉 원하시는 모드 번호를 선택하세요 (1 또는 2): ").strip()
    
    if menu == "1":
        run_pure_test()
    elif menu == "2":
        process_direct_comment_mode()
    else:
        print("❌ 올바른 선택이 아닙니다. 프로그램을 종료합니다.")