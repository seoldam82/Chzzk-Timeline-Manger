import sys
import json
import requests
import re
import os
from collections import defaultdict

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
    import os
    import sys
    import shutil
    import tempfile
    
    nid_aut = CONFIG.get("NID_AUT", "")
    nid_ses = CONFIG.get("NID_SES", "")
    
    if nid_aut and nid_ses:
        return nid_aut, nid_ses
        
    print("🔍 브라우저 로그인 세션 우회 탐색을 시작합니다...")

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
        print(f"⚠️ 기존 댓글 목록 조회 중 오류: {e}")
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
            print("✅ [성공] VOD에 댓글이 성공적으로 등록되었습니다!")
            return True
        else:
            print(f"❌ [에러] 상태 코드: {response.status_code}")
            print(f"   서버 응답: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 통신 오류: {e}")
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
    except Exception:
        return []

def select_chzzk_vod(channel_id, limit=10):
    vod_list = get_chzzk_vod_list(channel_id, limit=limit)
    if not vod_list:
        print("❌ 유효한 VOD가 없거나 채널 ID가 잘못되었습니다.")
        return None, None, 0
        
    print("\n" + "="*75)
    print(f"🎬 최근 VOD 리스트 (총 {len(vod_list)}개 발견)")
    print("="*75)
    for idx, video in enumerate(vod_list):
        title = video.get("videoTitle", "제목 없음")
        duration = video.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        print(f"[{idx + 1:2d}] {title} ({hours}시간 {minutes}분)")
    print("="*75)
    
    while True:
        try:
            user_input = input(f"\n👉 분석할 영상의 번호를 입력하세요 (1~{len(vod_list)}): ").strip()
            selected_idx = int(user_input)
            
            if 1 <= selected_idx <= len(vod_list):
                selected_video = vod_list[selected_idx - 1]
                video_no = selected_video.get("videoNo")
                video_title = selected_video.get("videoTitle", "방송다시보기")
                video_duration = selected_video.get("duration", 0)
                print(f"\n🎯 [선택 완료] '{video_title}' 분석을 진행합니다.")
                return str(video_no), video_title, video_duration
            
            print(f"❌ 1에서 {len(vod_list)} 사이의 숫자를 입력해주세요.")
        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")

def download_chzzk_vod_chats(video_no, start_sec, end_sec):
    print(f"💬 치지직 VOD 채팅 데이터 파싱 및 화력 압축 시작... ({int(start_sec)}초 ~ {int(end_sec)}초 구간)")
    url = f"https://api.chzzk.naver.com/service/v1/videos/{video_no}/chats"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://chzzk.naver.com",
        "Referer": f"https://chzzk.naver.com/video/{video_no}"
    }
    
    current_time_ms = int(start_sec * 1000)
    end_time_ms = int(end_sec * 1000)
    time_blocks = defaultdict(list)
    next_page_token = None
    
    while current_time_ms < end_time_ms:
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
                if msg_time_ms > end_time_ms: break
                    
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
                
        except Exception:
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
    print(f"✅ 압축 최적화 완료: 입력 토큰 대상 채팅을 총 {len(compressed_lines)}줄로 슬림화했습니다.")
    return final_compressed_chat