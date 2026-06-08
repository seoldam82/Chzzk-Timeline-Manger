from datetime import datetime
import sys
import json
import requests
import re
import os
from collections import defaultdict

SHOW = True
def log(message, level="INFO", show=True):
    if show:
        print(f"{message}")

def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log("❌ 에러: config.json 파일을 찾을 수 없습니다.", level="ERROR", show=SHOW)
        sys.exit(1)
    except json.JSONDecodeError:
        log("❌ 에러: config.json 파일의 형식이 올바르지 않습니다.", level="ERROR", show=SHOW)
        sys.exit(1)

CONFIG = load_config()

def get_auto_chzzk_cookies():
    import os
    import sys
    import shutil
    import tempfile
    
    nid_aut = CONFIG.get("NID_AUT", "")
    nid_ses = CONFIG.get("NID_SES", "")
    
    if nid_aut and nid_ses:
        return nid_aut, nid_ses
        
    log("🔍 브라우저 로그인 세션 우회 탐색을 시작합니다...", level="INFO", show=SHOW)

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
                
                if os.path.exists(temp_edge_cookie):
                    os.remove(temp_edge_cookie)
        
        if not nid_aut or not nid_ses:
            cj = browser_cookie3.edge(domain_name='.naver.com')
            for cookie in cj:
                if cookie.name == 'NID_AUT': nid_aut = cookie.value
                elif cookie.name == 'NID_SES': nid_ses = cookie.value
    except Exception:
        pass

    if not nid_aut or not nid_ses:
        try:
            if sys.platform == "win32":
                chrome_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies")
                if os.path.exists(chrome_path):
                    temp_chrome_cookie = os.path.join(temp_dir, "chrome_tmp_cookies")
                    shutil.copyfile(chrome_path, temp_chrome_cookie)
                    
                    cj = browser_cookie3.chrome(cookie_file=temp_chrome_cookie, domain_name='.naver.com')
                    for cookie in cj:
                        if cookie.name == 'NID_AUT': nid_aut = cookie.value
                        elif cookie.name == 'NID_SES': nid_ses = cookie.value
                    
                    if os.path.exists(temp_chrome_cookie):
                        os.remove(temp_chrome_cookie)
            else:
                cj = browser_cookie3.chrome(domain_name='.naver.com')
                for cookie in cj:
                    if cookie.name == 'NID_AUT': nid_aut = cookie.value
                    elif cookie.name == 'NID_SES': nid_ses = cookie.value
        except Exception:
            pass

    return nid_aut, nid_ses

def get_chzzk_user_no(headers):
    url = "https://api.chzzk.naver.com/service/v1/user/me"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get("code") == 200 and data.get("content"):
                return data["content"].get("userNo")
    except Exception:
        pass
    return None

def find_my_existing_comment(video_no, user_no, headers):
    url = f"https://api.chzzk.naver.com/service/v1/videos/{video_no}/comments"
    params = {"size": 50, "sortType": "NEW"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("code") == 200 and data.get("content"):
                comments = data["content"].get("data", [])
                for comment in comments:
                    if comment.get("userNo") == user_no:
                        content_str = comment.get("content", "")
                        if "🤖" in content_str or "[00:" in content_str or "타임라인" in content_str:
                            return comment.get("commentId")
    except Exception as e:
        log(f"⚠️ 기존 댓글 목록 조회 중 오류: {e}", level="WARNING", show=SHOW)
    return None

def write_chzzk_comment(video_no, comment_text):
    video_no = str(video_no).strip()
    nid_aut, nid_ses = get_auto_chzzk_cookies()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Whale/4.37.378.12 Safari/537.36",
        "Cookie": f"NID_AUT={nid_aut}; NID_SES={nid_ses}",
        "Origin": "https://chzzk.naver.com",
        "Referer": f"https://chzzk.naver.com/video/{video_no}",
        "Content-Type": "application/json",
        "deviceid": "44029ab1-a205-41e3-b1f3-51d5fdd6d2fb",
        "front-client-platform-type": "PC",
        "front-client-product-type": "web",
        "x-nng-service-id": "chzzk"
    }

    url = f"https://apis.naver.com/nng_main/nng_comment_api/v1/type/STREAMING_VIDEO/id/{video_no}/comments"
    payload = {
        "attach": False,
        "commentAttaches": [],
        "commentType": "COMMENT",
        "content": comment_text,
        "parentCommentId": 0,
        "secret": False,
        "mentionedUserIdHash": "",
        "deviceType": "PC"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            log("✅ [성공] VOD에 댓글이 성공적으로 등록되었습니다!", show=SHOW)
            return True
        else:
            log(f"❌ [에러] 상태 코드: {response.status_code}", level="ERROR", show=SHOW)
            log(f"   서버 응답: {response.text}", level="ERROR", show=SHOW)
            return False
    except Exception as e:
        log(f"❌ 통신 오류: {e}", level="ERROR", show=SHOW)
        return False

def get_chzzk_vod_list(channel_id, limit=10):
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/videos"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://chzzk.naver.com",
        "Referer": f"https://chzzk.naver.com/video/{channel_id}"
    }
    params = {"sortType": "LATEST", "pagingIndex": 0, "size": limit}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200:
                return data.get("content", {}).get("data", [])
        return []
    except Exception as e:
        log(f"❌ VOD 리스트 조회 중 오류: {e}", level="ERROR", show=SHOW)
        return []

def select_chzzk_vod(channel_id, limit=10):
    vod_list = get_chzzk_vod_list(channel_id, limit=limit)
    if not vod_list:
        log("❌ 유효한 VOD가 없거나 채널 ID가 잘못되었습니다.", level="ERROR", show=SHOW)
        return None, None, 0
        
    log("\n" + "="*75, level="INFO", show=SHOW)
    log(f"🎬 VOD 리스트 (총 {len(vod_list)}개 발견)", level="INFO", show=SHOW)
    log("="*75, level="INFO", show=SHOW)
    for idx, video in enumerate(vod_list):
        title = video.get("videoTitle", "제목 없음")
        duration = video.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        log(f"[{idx + 1:2d}] {title} ({hours}시간 {minutes}분)", level="INFO", show=SHOW)
    log("="*75, level="INFO", show=SHOW)
    
    while True:
        try:
            user_input = input(f"\n👉 분석할 영상의 번호를 입력하세요 (1~{len(vod_list)}): ").strip()
            selected_idx = int(user_input)
            
            if 1 <= selected_idx <= len(vod_list):
                selected_video = vod_list[selected_idx - 1]
                video_no = selected_video.get("videoNo")
                video_title = selected_video.get("videoTitle", "방송다시보기")
                video_duration = selected_video.get("duration", 0)
                log(f"\n🎯 [선택 완료] '{video_title}' 분석을 진행합니다.", level="INFO", show=SHOW)
                return str(video_no), video_title, video_duration

            log(f"❌ 1에서 {len(vod_list)} 사이의 숫자를 입력해주세요.", level="ERROR", show=SHOW)
        except ValueError:
            log("❌ 올바른 숫자를 입력해주세요.", level="ERROR", show=SHOW)

def download_chzzk_vod_chats(video_no, start_sec, end_sec):
    cache_dir = os.path.join(os.getcwd(), "cache_chat", str(video_no))
    os.makedirs(cache_dir, exist_ok=True)
    
    full_cache_filename = f"chat_{video_no}_full.txt"
    full_cache_path = os.path.join(cache_dir, full_cache_filename)
    
    if not os.path.exists(full_cache_path):
        log(f"💬 치지직 VOD [{video_no}] 전체 채팅 데이터 최초 다운로드 및 화력 압축 시작...", level="INFO", show=SHOW)
        url = f"https://api.chzzk.naver.com/service/v1/videos/{video_no}/chats"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Origin": "https://chzzk.naver.com",
            "Referer": f"https://chzzk.naver.com/video/{video_no}"
        }
        
        current_time_ms = 0
        time_blocks = defaultdict(list)
        next_page_token = None
        
        while True:
            params = {"playerMessageTime": current_time_ms, "size": 100}
            if next_page_token:
                params["pageToken"] = next_page_token
                
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code != 200: break
                data = response.json()
                if data.get("code") != 200: break
                    
                content = data.get("content", {})
                video_chats = content.get("videoChats", [])
                if not video_chats:
                    current_time_ms += 10000
                    next_page_token = None
                    continue
                    
                last_chat_time = current_time_ms
                for chat in video_chats:
                    msg_time_ms = chat.get("playerMessageTime", 0)
                    last_chat_time = msg_time_ms
                    message = chat.get("content", "").strip()
                    
                    if message:
                        chat_sec = msg_time_ms // 1000
                        block_index = chat_sec // 10 
                        time_blocks[block_index].append((chat_sec, message))
                
                meta = content.get("meta", {})
                next_page_token = meta.get("nextPageToken")
                
                if not next_page_token or last_chat_time <= current_time_ms:
                    current_time_ms = max(current_time_ms + 1000, last_chat_time + 1)
                    next_page_token = None
                else:
                    current_time_ms = last_chat_time + 1
                    
            except Exception as e:
                log(f"❌ VOD 채팅 다운로드 중 오류: {e}", level="ERROR", show=SHOW)
                break

        if not time_blocks:
            return "이 구간에는 실시간 채팅 기록이 존재하지 않습니다."

        total_blocks = len(time_blocks)
        total_chats_count = sum(len(chats) for chats in time_blocks.values())
        avg_chats_per_block = total_chats_count / total_blocks if total_blocks > 0 else 1
        
        compressed_lines = []
        consecutive_laugh_count = 0

        for block_idx in sorted(time_blocks.keys()):
            chats_in_block = time_blocks[block_idx]
            block_firepower = len(chats_in_block)
            
            is_high_tension = block_firepower > (avg_chats_per_block * 1.5)
            sample_size = 3 if is_high_tension else 1
            
            unique_chats = []
            seen_messages = set()
            for sec, msg in chats_in_block:
                clean_msg = re.sub(r'\{?:[a-zA-Z0-9_]+:\}?', '', msg).strip()
                clean_msg = clean_msg.replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")
                clean_msg = clean_msg.replace('\n', ' ').replace('\r', ' ')
                
                clean_text = re.sub(r'[^가-힣a-zA-Z0-9ㅋㅎ]', '', clean_msg).strip()
                is_pure_laugh = bool(re.match(r'^[ㅋㅎ]+$', clean_text)) if clean_text else False
                
                if is_pure_laugh:
                    if consecutive_laugh_count >= 1: continue
                    consecutive_laugh_count += 1
                else:
                    consecutive_laugh_count = 0

                clean_msg = re.sub(r'ㅋ{4,}', 'ㅋㅋㅋ', clean_msg)
                clean_msg = re.sub(r'ㅎ{4,}', 'ㅎㅎㅎ', clean_msg)

                if not clean_msg.strip(): continue

                short_msg = clean_msg[:10]
                if short_msg not in seen_messages:
                    unique_chats.append((sec, clean_msg))
                    seen_messages.add(short_msg)
                    if len(unique_chats) >= sample_size: break
            
            for sec, msg in unique_chats:
                h = sec // 3600
                m = (sec % 3600) // 60
                s = sec % 60
                tension_tag = " 🔥" if is_high_tension and unique_chats.index((sec, msg)) == 0 else ""
                compressed_lines.append(f"[{h:02d}:{m:02d}:{s:02d}]{tension_tag} {msg}")

        final_compressed_chat = "\n".join(compressed_lines)
        
        try:
            with open(full_cache_path, "w", encoding="utf-8") as f:
                f.write(final_compressed_chat)
            log(f"💾 [캐시 저장 완료] VOD 전체 통합 캐시 파일 생성이 완료되었습니다: {os.path.join('cache_chat', str(video_no), full_cache_filename)}", level="INFO", show=SHOW)
        except Exception as save_err:
            log(f"⚠️ [캐시 저장 오류] 통합 캐시 파일을 생성하지 못했습니다: {save_err}", level="WARNING", show=SHOW)

    try:
        with open(full_cache_path, "r", encoding="utf-8") as f:
            full_chat_text = f.read()
    except Exception as read_err:
        log(f"⚠️ [Cache Read Error] 통합 캐시 파일을 읽을 수 없습니다: {read_err}", level="WARNING", show=SHOW)
        return "채팅 캐시 로드 실패"

    log(f"📁 [채팅 캐시 슬라이싱] 통합 채팅 캐시에서 구간 슬라이싱 중... ({int(start_sec)}초 ~ {int(end_sec)}초)", level="INFO", show=SHOW)
    
    sliced_lines = []
    for line in full_chat_text.splitlines():
        match = re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\]", line)
        if match:
            h, m, s = map(int, match.groups())
            line_sec = h * 3600 + m * 60 + s
            if start_sec <= line_sec <= end_sec:
                sliced_lines.append(line)
                
    if not sliced_lines:
        return "이 구간에는 실시간 채팅 기록이 존재하지 않습니다."
        
    return "\n".join(sliced_lines)

def load_category_mapping(mapping_file_path='category.json'):
    if not os.path.exists(mapping_file_path):
        return {}
    
    try:
        with open(mapping_file_path, 'r', encoding='utf-8-sig') as f:
            category_data = json.load(f)
            
            if isinstance(category_data, dict):
                category_data = [category_data]
                
            if isinstance(category_data, list):
                mapping = {}
                for item in category_data:
                    c_id = item.get('categoryId')
                    c_val = item.get('categoryValue')
                    if c_id is not None and c_val is not None:
                        mapping[str(c_id).strip()] = str(c_val).strip()
                return mapping
    except Exception:
        return {}
    
    return {}

def find_game_category(vod_id, category_mapping_path='category.json'):
    file_path = os.path.join('cache_clips', str(vod_id), 'clips_data.json')
    
    if not os.path.exists(file_path):
        return []

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []

    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        return []

    category_mapping = load_category_mapping(category_mapping_path)
    category_set = set()
    
    for item in data:
        if not item.get("clipUID"):
            continue
            
        category_type = item.get("categoryType")
        
        if category_type == "GAME":
            clip_category = item.get("clipCategory")
            
            if clip_category is not None and str(clip_category).strip() != "":
                clip_category_str = str(clip_category).strip()
            
                final_category = category_mapping.get(clip_category_str, clip_category_str)
                
                if final_category:
                    category_set.add(final_category)
        
    return list(category_set)

if __name__ == "__main__":
    result = find_game_category('13591994', category_mapping_path='category.json')
    print(result)
