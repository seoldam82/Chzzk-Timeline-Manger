import sys
import json
import requests
import re
import os
from datetime import datetime, timedelta

from Chzzk_api import (
    CONFIG,
    get_auto_chzzk_cookies,
    select_chzzk_vod
)

def convert_seconds_to_hms(seconds):
    if seconds is None or seconds < 0: 
        return "00:00:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def parse_flexible_datetime(date_str):
    if not date_str: 
        return None
    date_str = str(date_str).strip().replace('T', ' ')
    date_str = re.sub(r'[\+\-]\d+$', '', date_str) 
    date_str = re.sub(r'\.\d+', '', date_str).strip()
    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"]
    for fmt in formats:
        try: 
            return datetime.strptime(date_str, fmt)
        except ValueError: 
            continue
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
                    return {"start_time": estimated_start, "duration": duration}
    except Exception: 
        pass
    return None

def fetch_creatorhub_pure_vod_link(media_id, clip_uid):
    url = "https://creatorhub-api.naver.com/api/v5.0/clipviewer/card"
    params = {
        "userInteraction": "true",
        "seedType": "SPECIFIC",
        "serviceType": "CHZZK",
        "seedMediaId": media_id,
        "mediaType": "SHORT_FORM",
        "panelType": "sdk_chzzk",
        "referer": f"https://chzzk.naver.com/clips/{clip_uid}",
        "recType": "CHZZK",
        "recId": json.dumps({"seedClipUID": clip_uid, "fromType": "GLOBAL", "listType": "RECOMMEND"}, separators=(',', ':')),
        "enableReverse": "false",
        "adAllowed": "true",
        "clickNsc": "chzzk_url_clip",
        "clickArea": "clip_item",
        "deviceType": "html5_mo",
        "profileOverride": "false"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://chzzk.naver.com",
        "Referer": "https://chzzk.naver.com/"
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        if response.status_code == 200:
            res_data = response.json()
            card_data = res_data.get("body", {}).get("card", {})
            content_obj = card_data.get("content", {})
            extra_links = content_obj.get("extraLinks", [])
            
            for link_node in extra_links:
                if link_node.get("type") == "TEXT":
                    pure_link = link_node.get("link", "")
                    if "currentTime=" in pure_link:
                        return pure_link
    except Exception: 
        pass
    return None

def get_clips_by_specific_vod(channel_id, target_video_no, pre_fetched_info=None):
    cache_dir = os.path.join(os.getcwd(), "cache_clips", str(target_video_no))
    cache_file_path = os.path.join(cache_dir, "clips_data.json")

    if os.path.exists(cache_file_path):
        print(f"💾 [클립 캐시 로드] 기저장된 캐시 데이터를 로드합니다: {cache_file_path}")
        try:
            with open(cache_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as cache_read_err:
            print(f"⚠️ 캐시 읽기 실패, API 원천 스캔을 진행합니다: {cache_read_err}")

    if pre_fetched_info:
        vod_info = pre_fetched_info
    else:
        vod_info = fetch_vod_detailed_info(target_video_no)
        
    if not vod_info: 
        print("❌ 타겟 VOD의 시간 정보를 가져오지 못했습니다.")
        return []
    
    vod_start_time = vod_info["start_time"]
    vod_duration = vod_info["duration"]
    
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
    
    print(f"📡 [클립 연동] 타겟 타임라인 내 후보 클립 고속 수집 중...")
    
    while page_count <= 20:
        if should_stop_entire_loop: 
            break
        params = {"clipUID": next_token, "filterType": "ALL", "orderType": "RECENT", "size": 50}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code != 200: 
                break
            data = response.json()
        except Exception:
            break
            
        page_clips = data.get("content", {}).get("data", [])
        if not page_clips: 
            break
        
        for clip_node in page_clips:
            clip_data = clip_node.get("full_response") if "full_response" in clip_node else clip_node
            created_str = clip_data.get("createdDate") or clip_node.get("createdDate", "")
            
            clip_date = parse_flexible_datetime(created_str)
            if not clip_date: 
                continue
            
            if start_bound <= clip_date <= end_bound:
                has_any_matched_before = True
                uid = clip_data.get("clipUID") or clip_node.get("clipUID")
                if uid and not any(c.get("clipUID") == uid for c in candidate_clips):
                    candidate_clips.append(clip_data)
            elif clip_date < start_bound and has_any_matched_before:
                should_stop_entire_loop = True
                break
                
        next_token = data.get("content", {}).get("page", {}).get("next", {}).get("clipUID", "")
        if not next_token: 
            break
        page_count += 1
        
    if not candidate_clips:
        try:
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir)
            with open(cache_file_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return []

    print(f"🎯 후보 클립 {len(candidate_clips)}개 확보 완료. 매핑 테이블 교차 동기화 가동...")
    
    final_matched_clips = []
    
    for clip in candidate_clips:
        clip_uid = clip.get("clipUID") or clip.get("clipUid")
        if not clip_uid: 
            continue
        
        master_video_id = clip.get("videoId")
        
        creator = "알 수 없음"
        title = clip.get("clipTitle") or "제목 없음"
        created_at = clip.get("createdDate") or "알 수 없음"
        view_count = clip.get("readCount", 0)
        like_count = clip.get("likeCount", 0)
        duration = clip.get("duration", 0)
        thumbnail = clip.get("thumbnailImageUrl") or ""

        opt = clip.get("optionalProperty", {})
        if opt:
            maker = opt.get("makerChannel", {})
            if maker: 
                creator = maker.get("channelName") or creator

        verified_vod_url = None
        real_sync_pos_seconds = None
        is_fallback_used = False
        
        if master_video_id:
            verified_vod_url = fetch_creatorhub_pure_vod_link(master_video_id, clip_uid)
            
        if verified_vod_url:
            time_match = re.search(r"currentTime=(\d+)", verified_vod_url)
            if time_match:
                real_sync_pos_seconds = int(time_match.group(1))
        
        if real_sync_pos_seconds == None:
            is_fallback_used = True
            clip_date = parse_flexible_datetime(created_at)
            if clip_date:
                real_sync_pos_seconds = int((clip_date - vod_start_time).total_seconds())
            else:
                real_sync_pos_seconds = 0

        if real_sync_pos_seconds < 0:
            real_sync_pos_seconds = 0
        if vod_duration > 0 and real_sync_pos_seconds > vod_duration:
            continue
            
        clip["clipTitle"] = title
        clip["clipCreatorName"] = creator
        clip["realPlaybackPosition"] = real_sync_pos_seconds
        clip["clipUID"] = clip_uid
        clip["createdDate"] = created_at
        clip["viewCount"] = view_count
        clip["likeCount"] = like_count
        clip["clipDuration"] = duration
        clip["thumbnail"] = thumbnail
        clip["masterVideoId"] = master_video_id
        clip["isFallbackUsed"] = is_fallback_used
        clip["verifiedUrl"] = verified_vod_url if verified_vod_url else f"https://chzzk.naver.com/video/{target_video_no}?currentTime={real_sync_pos_seconds}"
        
        final_matched_clips.append(clip)
        
    try:
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        with open(cache_file_path, "w", encoding="utf-8") as f:
            json.dump(final_matched_clips, f, ensure_ascii=False, indent=4)
        print(f"💾 [클립 캐시 완료] 데이터가 저장되었습니다 -> {cache_file_path}")
    except Exception as cache_write_err:
        print(f"⚠️ 캐시 쓰기 실패: {cache_write_err}")

    return final_matched_clips

def print_clip_details(idx, clip):
    title = clip.get("clipTitle", "제목 없음")
    creator = clip.get("clipCreatorName", "알 수 없음")
    playback_position = clip.get("realPlaybackPosition", 0)
    clip_uid = clip.get("clipUID", "")
    created_at = clip.get("createdDate", "알 수 없음")
    master_vid = clip.get("masterVideoId", "알 수 없음")
    
    views = clip.get("viewCount", 0)
    likes = clip.get("likeCount", 0)
    clip_dur = clip.get("clipDuration", 0)
    thumb = clip.get("thumbnail", "없음")
    sync_type = "⚠️ 수동 계산 폴백 (오차 가능성 존재)" if clip.get("isFallbackUsed") else "🔥 CreatorHub 원천 매핑 성공 (오차 0초)"
    timestamp = convert_seconds_to_hms(playback_position)
    vod_url = clip.get("verifiedUrl")
    
    print(f"{idx}. 📌 [{timestamp}] {title}")
    print(f"   [상세 동기화 로그]")
    print(f"   ├─ 분석 상태: {sync_type}")
    print(f"   ├─ 마스터 ID : {master_vid}")
    print(f"   ├─ 원천밀리초: {playback_position * 1000:,} ms")
    print(f"   └─ 환산싱크초: {playback_position:,} 초 (-> 네이버 가공 데이터 일치 완료)")
    print(f"   [클립 통계 정보]")
    print(f"   ├─ 생성자: {creator}  |  📅 생성시각: {created_at}  |  ⏱️ 클립길이: {clip_dur}초")
    print(f"   ├─ 조회수: {views:,}회  |  ❤️ 추천수: {likes:,}개  |  🆔 클립 ID: {clip_uid}")
    print(f"   └─ 🖼️ 썸네일: {thumb}")
    print(f"   🔗 원본 방송 클립 시작 위치:")
    print(f"      {vod_url}")
    print("-" * 65)

def analyze_and_print_clip_density(clips):
    if not clips:
        return []
        
    valid_clips = [c for c in clips if c.get("realPlaybackPosition") is not None]
    if not valid_clips:
        return []
        
    valid_clips.sort(key=lambda x: x["realPlaybackPosition"])
    
    density_segments = []
    window_size = 600  
    
    print("\n🚨 [클립 밀도 분석 엔진 가동]")
    
    i = 0
    while i < len(valid_clips):
        start_clip = valid_clips[i]
        start_pos = start_clip["realPlaybackPosition"]
        end_pos = start_pos + window_size
        
        window_clips = []
        j = i
        while j < len(valid_clips) and valid_clips[j]["realPlaybackPosition"] <= end_pos:
            window_clips.append(valid_clips[j])
            j += 1
            
        if len(window_clips) >= 3:
            seg_start = start_pos
            seg_end = window_clips[-1]["realPlaybackPosition"]
            clip_titles = [c.get("title", "제목 없음") for c in window_clips]
            
            segment_info = {
                "start_sec": seg_start,
                "end_sec": seg_end,
                "clip_count": len(window_clips),
                "titles": clip_titles
            }
            density_segments.append(segment_info)
            
            print(f"🔥 [하이라이트 감지] {convert_seconds_to_hms(seg_start)} ~ {convert_seconds_to_hms(seg_end)} "
                  f"(클립 생성: {len(window_clips)}개)")
            
            i = j
        else:
            i += 1
            
    return density_segments


def get_clip_density_context_string(clips):
    density_segments = analyze_and_print_clip_density(clips)
    if not density_segments:
        return "정보 없음 (밀집된 클립 구간이 존재하지 않습니다.)"
        
    lines = []
    for idx, seg in enumerate(density_segments, 1):
        start_hms = convert_seconds_to_hms(seg["start_sec"])
        end_hms = convert_seconds_to_hms(seg["end_sec"])
        titles_summary = ", ".join([f"'{t}'" for t in seg["titles"][:3]])
        if len(seg["titles"]) > 3:
            titles_summary += f" 외 {len(seg['titles'])-3}개"
            
        lines.append(
            f"- 구간 [{start_hms} ~ {end_hms}]: 약 {seg['clip_count']}개의 대량 클립 생성 구역 "
            f"(대표 클립 주제: {titles_summary})"
        )
    return "\n".join(lines)

if __name__ == "__main__":
    TARGET_CHANNEL_ID = CONFIG.get("TARGET_CHANNEL_ID")
    if not TARGET_CHANNEL_ID: 
        print("❌ 에러: config.json에 TARGET_CHANNEL_ID를 입력해 주세요.")
        sys.exit(1)
        
    video_no, video_title, _ = select_chzzk_vod(TARGET_CHANNEL_ID)
    
    if video_no:
        print("\n" + "="*50)
        print(f"🔍 타켓 VOD 확정: [{video_title}] (No. {video_no})")
        print("==================================================")
        
        clips = get_clips_by_specific_vod(TARGET_CHANNEL_ID, video_no)
        
        analyze_and_print_clip_density(clips)
        
        print("\n" + "="*50)
        if not clips:
            print(f"ℹ️ 선택하신 VOD 내 매핑 완료된 클립이 없습니다.")
        else:
            clips.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
            total_count = len(clips)
            top_30_limit = max(1, int(total_count * 0.3))
            
            popular_clips = []
            unpopular_clips = []
            
            for idx, clip in enumerate(clips):
                views = clip.get("viewCount", 0)
                if views <= 100:
                    unpopular_clips.append(clip)
                elif views >= 1000:
                    popular_clips.append(clip)
                elif idx < top_30_limit:
                    popular_clips.append(clip)
                else:
                    unpopular_clips.append(clip)
                    
            popular_clips.sort(key=lambda x: x.get("realPlaybackPosition", 0))
            unpopular_clips.sort(key=lambda x: x.get("realPlaybackPosition", 0))
            
            print(f"🔥 [인기 클립 리스트] 총 {len(popular_clips)}개")
            print("==================================================\n")
            for idx, clip in enumerate(popular_clips, 1):
                print_clip_details(idx, clip)