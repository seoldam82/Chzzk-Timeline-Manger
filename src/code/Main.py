import os
import sys
import math
import signal
import re
import glob

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
    correct_streamer_nicknames_with_gemini,
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

def process_direct_comment_mode():
    print("\n--------------------------------------------------")
    print("📂 기존 타임라인 파일 불러오기 및 바로 댓글 등록 모드")
    print("--------------------------------------------------")
    
    txt_files = glob.glob("TL_VOD_*.txt")
    
    if not txt_files:
        print("❌ 현재 디렉토리에 'TL_VOD_...' 형식으로 생성된 텍스트 파일이 없습니다.")
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

    vod_id_match = re.search(r"TL_VOD_(\d+)_", selected_file)
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

    print("\n==================================================")
    print(f"📖 선택한 파일 [{selected_file}] 내용 미리보기")
    print("==================================================")
    print(comment_content[:1000]) 
    if len(comment_content) > 1000:
        print("\n... (이하 생략) ...")
    print("==================================================")
    
    timeline_len = len(comment_content)
    print(f"📊 현재 로드된 타임라인 글자 수: {timeline_len}자 / 5000자")

    if timeline_len > 5000:
        print("⚠️ [경고] 타임라인 총 길이가 5000자를 초과하여 치지직 댓글 제한에 걸립니다.")
        print("💡 텍스트 파일을 직접 열어 내용을 5000자 이내로 줄여주신 다음 다시 시도해 주세요.")
        return

    answer = input(f"\n🚀 이 타임라인을 VOD [{vod_id}]번에 즉시 반영(등록/수정)할까요? (y/n): ").strip().lower()
    if answer == 'y':
        write_chzzk_comment(video_no=vod_id, comment_text=comment_content)
    else:
        print("👋 작업을 취소했습니다.")


def run_pure_test():
    print("\n--------------------------------------------------")
    print("🤖 AI 기반 새 VOD 타임라인 생성 및 추출 모드 시작")
    print("--------------------------------------------------")
    
    TARGET_CHANNEL_ID = CONFIG.get("TARGET_CHANNEL_ID")
    GEMINI_API_KEY = CONFIG.get("GEMINI_API_KEY")
    WHISPER_MODEL = CONFIG.get("WHISPER_MODEL", "base")
    
    if not GEMINI_API_KEY or GEMINI_API_KEY.strip() == "":
        print("❌ Gemini API 키가 설정되지 않았습니다. config.json 파일의 'GEMINI_API_KEY' 항목을 확인하세요.")
        return

    voicepalette_BASE_DIR = "./voicepalette"
    if not os.path.exists(voicepalette_BASE_DIR):
        os.makedirs(voicepalette_BASE_DIR)

    vod_id, actual_title, video_duration = select_chzzk_vod(TARGET_CHANNEL_ID)
    if not vod_id:
        print("❌ 유효한 치지직 VOD 일련번호를 획득하지 못했습니다.")
        return

    full_vod_url = f"[https://chzzk.naver.com/video/](https://chzzk.naver.com/video/){vod_id}"
    print(f"\n🎬 선택된 타겟 방송: [{actual_title}] (VOD ID: {vod_id})")
    
    try:
        start_percent = float(input("➡️ 전체 분석 시작 시간 백분율을 입력하세요 (예: 0 -> 처음부터): ").strip())
        end_percent = float(input("➡️ 전체 분석 종료 시간 백분율을 입력하세요 (예: 100 -> 끝까지): ").strip())
    except ValueError:
        print("❌ 올바른 숫자를 입력하세요. 기본값(0% ~ 100%)으로 매핑하여 시작합니다.")
        start_percent = 0.0
        end_percent = 100.0

    total_duration_secs = video_duration if video_duration > 0 else 14400
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
    CHUNK_SIZE_SECS = 3600
    all_raw_items = []
    
    current_chunk_start = global_start_sec
    while current_chunk_start < global_end_sec:
        current_chunk_end = min(current_chunk_start + CHUNK_SIZE_SECS, global_end_sec)
        chunk_index = int(current_chunk_start // CHUNK_SIZE_SECS)
        
        print(f"\n[🔄 청크 슬라이싱] {current_chunk_start}초 ~ {current_chunk_end}초 구간 텍스트 추출 중... (인덱스: {chunk_index})")

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

        print(f"🚀 Gemini AI 원격 모델 호출 중 (청크 인덱스: {chunk_index})...")
        chunk_items = generate_chzzk_timeline(
            input_script=chunk_transcription_text,
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

    # 1. 1차 취합 데이터 문자열 빌드
    final_output_text = merge_and_format_final_timeline(all_raw_items)

    # 🚨 [최종단계 Gemini 보정] 문맥 분석 권한을 위임받은 함수 호출 수행
    print("⚙️  [최종 후처리] Gemini 모델을 활용한 문맥/유사도 기반 닉네임 교정 작업 수행 중...")
    final_output_text = correct_streamer_nicknames_with_gemini(
        timeline_text=final_output_text, 
        api_key=GEMINI_API_KEY, 
        db_filename="chzzk_streamers.txt"
    )

    ai_notice = "🤖 이 댓글은 방송 하이라이트를 AI가 분석하여 생성한 타임라인으로 다소 부정확한 부분이 있을 수 있습니다."
    new_header = f"[00:00:00] {actual_title}"

    lines = final_output_text.split("\n")
    cleaned_final_lines = []
    
    for line in lines:
        line_strip = line.strip().replace("🔥", "") 
        line_strip = re.sub(r'\[\[(\d{2}:\d{2}:\d{2})\]\]', r'[\1]', line_strip)
        line_strip = re.sub(r'\[\[(\d{2}:\d{2})\]\]', r'[\1]', line_strip)
        
        if "🤖 이 댓글은" in line_strip or line_strip.startswith("[00:00:00]"):
            continue
        cleaned_final_lines.append(line_strip)
        
    cleaned_final_lines.insert(0, "")
    cleaned_final_lines.insert(0, new_header)
    cleaned_final_lines.insert(0, ai_notice)

    final_timeline_string = "\n".join(cleaned_final_lines)
    output_path = f"TL_VOD_{vod_id}_{int(start_percent)}_{int(end_percent)}.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_timeline_string)

    print("\n==================================================")
    print("🎯 [완성] 모든 청크 취합 및 가공이 완료된 최종 타임라인 결과")
    print("==================================================")
    print(final_timeline_string)
    print("==================================================")
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
    
    print("==================================================")
    print("        치지직 VOD 타임라인 매니저 ver 1.0        ")
    print("==================================================")
    print(" [1] AI 연산 실행하여 새 타임라인 파일 생성하기")
    print(" [2] 이미 만들어진 로컬 타임라인 파일 불러와 즉시 댓글 등록하기")
    print("--------------------------------------------------")
    
    menu = input("👉 원하시는 모드 번호를 선택하세요 (1 또는 2): ").strip()
    
    if menu == "1":
        run_pure_test()
    elif menu == "2":
        process_direct_comment_mode()
    else:
        print("❌ 올바른 선택이 아닙니다. 프로그램을 종료합니다.")