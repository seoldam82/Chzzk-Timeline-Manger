import os
import json
import requests

def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("config.json 파일을 찾을 수 없습니다. 기본 설정을 사용합니다.")
        return {}
    except Exception as e:
        print(f"config.json 로드 중 에러 발생: {e}")
        return {}

def load_existing_categories():
    if os.path.exists('category.json'):
        try:
            with open('category.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
        except Exception as e:
            print(f"category.json 읽기 오류 (새로 생성합니다): {e}")
    return []

def save_categories(categories):
    try:
        with open('category.json', 'w', encoding='utf-8') as f:
            json.dump(categories, f, ensure_ascii=False, indent=4)
        print(f"성공적으로 category.json에 저장되었습니다. (총 {len(categories)}개 수집됨)")
    except Exception as e:
        print(f"category.json 저장 중 에러 발생: {e}")

def fetch_live_categories():
    config = load_config()
    cookies = {
        "NID_SES": config.get("NID_SES"),
        "NID_AUT": config.get("NID_AUT")
    }
    
    url = "https://api.chzzk.naver.com/service/v1/lives"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://chzzk.naver.com/"
    }
    
    live_categories = {}
    size = 50  
    next_page_params = None
    
    print("치지직 실시간 라이브 카테고리 수집을 시작합니다...")
    
    while True:
        params = {
            "size": size,
            "sortType": "POPULAR" 
        }
        
        if next_page_params and isinstance(next_page_params, dict):
            params.update(next_page_params)
            
        try:
            res = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=5)
            if res.status_code == 200:
                content = res.json().get("content", {})
                live_list = content.get("data", [])
                
                if not live_list:
                    break
                    
                for live in live_list:
                    live_category_value = live.get("liveCategoryValue")
                    
                    if live.get("liveCategory") == "game" or live.get("categoryType") == "GAME":
                        cval = live_category_value
                        cid = live.get("liveCategory")
                        
                        if cval and cval not in live_categories:
                            live_categories[cval] = {
                                "categoryId": cid if cid else "",
                                "categoryValue": cval,
                                "categoryType": live.get("categoryType", "GAME")
                            }
                
                page_options = content.get("page", {})
                next_page_params = page_options.get("next", None)
                
                if not next_page_params:
                    break

            else:
                print(f"API 호출 실패: {res.status_code}")
                break
        except Exception as e:
            print(f"에러 발생: {e}")
            break
            
    return list(live_categories.values())

def update_category_file():
    existing_categories = load_existing_categories()
    
    existing_values = {item.get("categoryValue") for item in existing_categories if item.get("categoryValue")}
    
    current_live_categories = fetch_live_categories()

    added_count = 0
    for item in current_live_categories:
        cval = item.get("categoryValue")
        if cval and cval not in existing_values:
            existing_categories.append(item)
            existing_values.add(cval)
            print(f"[신규 카테고리 추가] {cval}")
            added_count += 1
            
    print(f"\n--- 업데이트 결과 ---")
    print(f"새로 추가된 카테고리: {added_count}개")
    
    if added_count > 0:
        save_categories(existing_categories)
    else:
        print("새롭게 추가된 카테고리가 없습니다. 기존 파일을 유지합니다.")

if __name__ == "__main__":
    update_category_file()