import sys
import json
import requests
import re
import os
from datetime import datetime, timedelta

def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ 에러: config.json 파일을 찾을 수 없습니다.")
        sys.exit(1)
    except json.JSONDecodeError:
        print("❌ 에러: config.json 파일의 형식이 올바르지 않습니다.")
        sys.exit(1)

CONFIG = load_config()

def get_auto_chzzk_cookies():
    import shutil
    import tempfile
    
    nid_aut = CONFIG.get("NID_AUT", "")
    nid_ses = CONFIG.get("NID_SES", "")
    
    if nid_aut and nid_ses:
        return nid_aut, nid_ses
        
    try:
        import browser_cookie3
    except ImportError:
        return "", ""

    temp_dir = tempfile.gettempdir()
    try:
        if sys.platform == "win32":
            edge_path = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies")
            if os.path.exists(edge_path):
                temp_edge_cookie = os.path.join(temp_dir, "edge_tmp_cookies")
                shutil.copyfile(edge_path, temp_edge_cookie)
                cj = browser_cookie3.edge(cookie_file=temp_edge_cookie, domain_name='.naver.com')
                for cookie in cj:
                    if cookie.name == 'NID_AUT': nid_aut = cookie.value
                    elif cookie.name == 'NID_SES': nid_ses = cookie.value
                if os.path.exists(temp_edge_cookie): os.remove(temp_edge_cookie)
    except Exception: pass
    return nid_aut, nid_ses

def get_chzzk_vod_list(channel_id, limit=10):
    nid_aut, nid_ses = get_auto_chzzk_cookies()
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/videos"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"NID_AUT={nid_aut}; NID_SES={nid_ses}"
    }
    params = {"sortType": "LATEST", "pagingIndex": 0, "size": limit}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200:
                return data.get("content", {}).get("data", [])
        return []
    except Exception:
        return []

def select_chzzk_vod(channel_id):
    vod_list = get_chzzk_vod_list(channel_id)
    if not vod_list:
        print("❌ 유효한 VOD가 없거나 채널 ID가 잘못되었습니다.")
        return None, None, 0
        
    print("\n==================================================")
    print(f"🎬 최근 VOD 리스트")
    print("==================================================")
    for idx, video in enumerate(vod_list):
        title = video.get("videoTitle", "제목 없음")
        duration = video.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        print(f"[{idx}] {title} ({hours}시간 {minutes}분)")
    print("==================================================")
    
    while True:
        try:
            user_input = input("\n👉 분석할 영상의 번호를 입력하세요: ").strip()
            selected_idx = int(user_input)
            if 0 <= selected_idx < len(vod_list):
                selected_video = vod_list[selected_idx]
                video_no = selected_video.get("videoNo")
                video_title = selected_video.get("videoTitle", "방송다시보기")
                video_duration = selected_video.get("duration", 0)
                return str(video_no), video_title, video_duration
            print("❌ 범위 안의 올바른 숫자를 입력하세요.")
        except ValueError:
            print("❌ 숫자만 입력 가능합니다.")

def convert_seconds_to_hms(seconds):
    if seconds is None or seconds < 0: return "00:00:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def parse_flexible_datetime(date_str):
    if not date_str: return None
    date_str = str(date_str).strip().replace('T', ' ')
    date_str = re.sub(r'[\+\-]\d+$', '', date_str) 
    date_str = re.sub(r'\.\d+', '', date_str).strip()
    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"]
    for fmt in formats:
        try: return datetime.strptime(date_str, fmt)
        except ValueError: continue
    return None

def fetch_vod_detailed_info(target_vod_id):
    nid_aut, nid_ses = get_auto_chzzk_cookies()
    url = f"https://api.chzzk.naver.com/service/v1/videos/{target_vod_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"NID_AUT={nid_aut}; NID_SES={nid_ses}"
    }
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("content"):
                content = data["content"]
                publish_date_str = content.get("videoDate") or content.get("publishDate")
                duration = content.get("duration", 0)
                
                publish_time = parse_flexible_datetime(publish_date_str)
                if publish_time:
                    estimated_start = publish_time - timedelta(seconds=duration)
                    return estimated_start
    except Exception: pass
    return None

def get_clips_by_specific_vod(channel_id, target_vod_id):
    vod_start_time = fetch_vod_detailed_info(target_vod_id)
    if not vod_start_time: 
        print("❌ 타겟 VOD의 시간 정보를 가져오지 못했습니다.")
        return []
    
    start_bound = vod_start_time - timedelta(hours=3)
    end_bound = vod_start_time + timedelta(hours=24)

    nid_aut, nid_ses = get_auto_chzzk_cookies()
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/clips"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"NID_AUT={nid_aut}; NID_SES={nid_ses}"
    }
    
    candidate_clips = []
    has_any_matched_before = False
    should_stop_entire_loop = False
    next_token = ""
    page_count = 1
    
    print(f"   -> 🔎 [1단계: 타겟 타임라인 내 후보 클립 고속 수집 중]...")
    
    while page_count <= 20:
        if should_stop_entire_loop: break
        params = {"clipUID": next_token, "filterType": "ALL", "orderType": "RECENT", "size": 50}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code != 200: break
        
        data = response.json()
        page_clips = data.get("content", {}).get("data", [])
        if not page_clips: break
        
        for clip in page_clips:
            clip_date = parse_flexible_datetime(clip.get("createdDate", ""))
            if not clip_date: continue
            
            if start_bound <= clip_date <= end_bound:
                has_any_matched_before = True
                if not any(c.get("clipUID") == clip.get("clipUID") for c in candidate_clips):
                    candidate_clips.append(clip)
            elif clip_date < start_bound and has_any_matched_before:
                should_stop_entire_loop = True
                break
                
        next_token = data.get("content", {}).get("page", {}).get("next", {}).get("clipUID", "")
        if not next_token: break
        page_count += 1
        
    if not candidate_clips:
        return []

    print(f"   -> 🎯 후보 클립 {len(candidate_clips)}개 확보 완료.")
    print(f"   -> ⚡ [2단계: 절대 시간차 동기화 연산 매핑 가동]...\n")
    
    final_matched_clips = []
    
    for clip in candidate_clips:
        clip_date = parse_flexible_datetime(clip.get("createdDate", ""))
        creator = clip.get("ownerNickname", "알 수 없음")
        
        time_diff = clip_date - vod_start_time
        diff_seconds = int(time_diff.total_seconds())
        
        real_playback_pos = diff_seconds - 20
        
        if real_playback_pos < 0:
            real_playback_pos = 0
            
        clip["clipCreatorName"] = creator
        clip["realPlaybackPosition"] = real_playback_pos
        final_matched_clips.append(clip)
        
    return final_matched_clips

if __name__ == "__main__":
    TARGET_CHANNEL_ID = CONFIG.get("TARGET_CHANNEL_ID")
    if not TARGET_CHANNEL_ID: sys.exit(1)
        
    video_no, video_title, _ = select_chzzk_vod(TARGET_CHANNEL_ID)
    
    if video_no:
        print("\n" + "="*50)
        print(f"🔍 타겟 VOD 확정: [{video_title}] (No. {video_no})")
        print("==================================================")
        
        clips = get_clips_by_specific_vod(TARGET_CHANNEL_ID, video_no)
        clips.sort(key=lambda x: x.get("realPlaybackPosition", 0))
        
        print("\n" + "="*50)
        if not clips:
            print(f"ℹ️ 선택하신 VOD 구역 내 유저 클립이 없습니다.")
        else:
            print(f"✅ [정제 완료] 차단 우회 및 타임라인 100% 동기화 리스트 ({len(clips)}개)")
            print("==================================================\n")
            
            for idx, clip in enumerate(clips, 1):
                title = clip.get("clipTitle", "제목 없음")
                creator = clip.get("clipCreatorName", "알 수 없음")
                playback_position = clip.get("realPlaybackPosition", 0)
                created_at = clip.get("createdDate", "")
                
                timestamp = convert_seconds_to_hms(playback_position)
                vod_url = f"https://chzzk.naver.com/video/{video_no}?currentTime={playback_position}"
                
                print(f"{idx}. 📌 [{timestamp}] {title}")
                print(f"   👤 제작자: {creator}  |  📅 생성시각: {created_at}")
                print(f"   🔗 VOD 이동 좌표: {vod_url}")
                print("-" * 50)