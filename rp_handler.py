import os
import time
import requests
import json
import runpod
import traceback
import base64
import uuid

import websocket # comfyui  완료 감지
import requests
from requests.adapters import HTTPAdapter, Retry
# ComfyUI API 호출
import urllib.request  

from datetime import datetime
import pymysql



COMFYUI_URL = 'http://127.0.0.1:8188'
RUNPOD_VOLUME_PATH = '/runpod-volume'
VOLUME_MOUNT_PATH  = os.environ.get("RUNPOD_VOLUME_PATH", "/runpod-volume")
NETWORK_VOLUME = '/workspace'
TIMEOUT = 600
# ------------------------------DB 연결 정보----------------------------------- #
DB_HOST     = os.environ.get("DB_HOST")
DB_PORT     = int(os.environ.get("DB_PORT", 3306))
DB_USER     = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME     = os.environ.get("DB_NAME")
# ----------------------------------------------------------------------------- #
session = requests.Session()
retries = Retry(total=10, backoff_factor=0.1, status_forcelist=[502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))
print("start worker")



# ---------------------------------------------------------------------------- #
#                               DB Functions                                   #
# ---------------------------------------------------------------------------- #

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def db_insert(conn, customer_id, simulation_id):
    """
    job 시작 시 INSERT
    image_statement = 0 (진행중)
    image_url = NULL (아직 없음)
    create_at / update_at = DB DEFAULT (CURRENT_TIMESTAMP)
    """
    try:
        with conn.cursor() as cursor:
            sql = """
                INSERT INTO comfyui_renderimage_statement
                    (customer_id, simulation_id, image_statement)
                VALUES
                    (%s, %s, 0)
            """
            cursor.execute(sql, (customer_id, simulation_id))
            conn.commit()
            image_id = cursor.lastrowid
            print(f"[DB] INSERT 완료 - image_id: {image_id}")
            return image_id
    except Exception as e:
        print(f"[DB] INSERT 실패: {e}")
        raise


def db_update(conn, image_id, image_statement, image_url=None):
    """
    완료(2) 또는 에러(3) 시 UPDATE
    update_at은 DB의 ON UPDATE CURRENT_TIMESTAMP가 자동 처리
    """
    try:
        with conn.cursor() as cursor:
            sql = """
                UPDATE comfyui_renderimage_statement
                SET image_statement = %s,
                    image_url = %s
                WHERE image_id = %s
            """
            cursor.execute(sql, (image_statement, image_url, image_id))
            conn.commit()
            print(f"[DB] UPDATE 완료 - image_id: {image_id}, statement: {image_statement}")
    except Exception as e:
        print(f"[DB] UPDATE 실패: {e}")
        raise


# ---------------------------------------------------------------------------- #
#                               ComfyUI Functions                              #
# ---------------------------------------------------------------------------- #

def save_input_image(image_base64, customer_id, simulation_id, file_uuid):
    """
    base64 이미지를 /tmp/input/{customer_id}/{simulation_id}에 저장
    파일명 : {file_uuid}.png
    """
    # 입력 폴더 (로컬 tmp, 휘발성)
    input_dir = f"/tmp/input/{customer_id}/{simulation_id}"
    os.makedirs(input_dir, exist_ok=True)

    filename = f"{file_uuid}.png"
    input_image_path = os.path.join(input_dir, filename) # input_image 경로

    # image를 base64로 받기
    image_bytes = base64.b64decode(image_base64)
    with open(input_image_path, "wb") as f:
        f.write(image_bytes)

    # 출력 폴더 (Network Volume, 영구)
    output_dir = f"{VOLUME_MOUNT_PATH}/runpod-slim/ComfyUI/output/{customer_id}/{simulation_id}/{file_uuid}"
    os.makedirs(output_dir, exist_ok=True)

    # 출력 파일 경로: ComfyUI가 {prefix}_0001.png 로 저장함
    save_image_path = os.path.join(output_dir, f"{file_uuid}_0001.png")

    return input_dir, output_dir, input_image_path, save_image_path


#  workflow 수정
def get_workflow(input_dir, output_dir, file_uuid):
    """
    qwen_model_1229_Fair_blending_websocket_0402_del_segment.json
    input_dir 및 output_dir 경로 현재 job 경로로 교체
    """
    workflow_path = f"{VOLUME_MOUNT_PATH}/runpod-slim/ComfyUI/user/default/workflows/api_qwen_model_1229_Fair_blending_websocket_0402_del_segment_diffusion.json"
    
    if not os.path.exists(workflow_path):
        raise FileNotFoundError(f"workflow 파일 없음: {workflow_path}")
    
    with open(workflow_path, "r") as f:
        workflow = json.load(f)

    # 노드 23: 입력 경로 교체
    workflow["23"]["inputs"]["path"] = input_dir

    # 노드 51: 출력 경로 교체
    workflow["51"]["inputs"]["output_path"] = output_dir
    
    # 파일명에 index 추가 (GPU 여러 개가 같은 폴더에 저장할 때 충돌 방지)
    workflow["51"]["inputs"]["filename_prefix"] = file_uuid

    return workflow




# Comfyui 실행
def queue_prompt(workflow):
    """workflow를 ComfyUI 큐에 전송 → prompt_id 반환"""
    payload = {"prompt": workflow}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())





def wait_for_completion(prompt_id, ws, timeout=600):
    start_time = time.time()
    try:
        while time.time() - start_time < timeout:
            ws.settimeout(1)
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                # ✅ timeout마다 history API로 완료 여부 이중 체크
                try:
                    r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=3)
                    if r.status_code == 200 and prompt_id in r.json():
                        print("[WS] history API로 완료 확인!")
                        return True
                except:
                    pass
                continue

            if not isinstance(msg, str):
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")
            msg_data = data.get("data", {})
            print(f"[WS] type={msg_type}, data={msg_data}")

            # ✅ 기존 방식
            if msg_type == "executing":
                node = msg_data.get("node")
                pid  = msg_data.get("prompt_id")
                if node is None and pid == prompt_id:
                    print("[WS] 완료 감지!")
                    return True

            # ✅ 추가: queue_remaining=0 이면 history API로 최종 확인
            if msg_type == "status":
                queue_remaining = msg_data.get("status", {}).get("exec_info", {}).get("queue_remaining", -1)
                if queue_remaining == 0:
                    try:
                        r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=3)
                        if r.status_code == 200 and prompt_id in r.json():
                            print("[WS] queue_remaining=0 + history 확인 → 완료!")
                            return True
                    except:
                        pass

    except Exception as e:
        print(f"[WS] 예외 발생: {e}")
        traceback.print_exc()
    finally:
        ws.close()

    print("[WS] Timeout 또는 루프 종료")
    return False



def wait_for_comfyui(timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=3)
            if r.status_code == 200:  # 이미 200 체크하고 있으니 OK
                print("[ComfyUI] 서버 준비 완료")
                return True
        except:
            pass
        print("[ComfyUI] 대기 중...")
        time.sleep(3)
    raise RuntimeError("ComfyUI 서버 시작 실패")



def load_image_as_base64(save_image_path, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(save_image_path) and os.path.getsize(save_image_path) > 0:
            with open(save_image_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            print(f"[7] base64 변환 완료: {save_image_path}")
            return encoded
        time.sleep(0.5)
    raise FileNotFoundError(f"출력 이미지가 생성되지 않았습니다: {save_image_path}")






# 메인 handler
def handler(job):
    """
    RunPod이 job 수신 시 자동으로 이 함수를 호출
    job = {
        "id": "job_xxx",
        "input": {
            "image_base64": <base64문자열>,
            "customer_id": "cust_001",
            "simulation_id": "sim_002"
        }
    }
    """
    job_input = job.get("input", {})

    # ── 입력값 파싱 ──────────────────
    image_base64     = job_input.get("image_base64")
    customer_id   = job_input.get("customer_id")
    simulation_id = job_input.get("simulation_id")


    # ⭐ image_base64
    if not image_base64:
        return {"error": "image_base64 필요"}

    if not all([customer_id, simulation_id]):
        return {"error": "customer_id, simulation_id 필요"}

    # ✅ uuid 자동 생성 (예: "a3f2c1d4")
    file_uuid = uuid.uuid4().hex[:8]

    job_start = time.time()  # 전체 시작
    print(f"[0] 생성된 uuid: {file_uuid}")


    # ── DB INSERT (job 시작) ──────────────────
    conn = None
    image_id = None

    
    try:
        t = time.time()
        conn = get_db_connection()  # 딱 1번만 연결
        image_id = db_insert(conn, customer_id, simulation_id)
        print(f" DB INSERT: {time.time()-t:.2f}초")
    except Exception as e:
        print(f"[DB] INSERT 실패, 계속 진행: {e}")
    
    
    try:
        t = time.time()
        print("[1] 입력 이미지 저장 시작")
        input_dir, output_dir, input_image_path, save_image_path = save_input_image(
            image_base64, customer_id, simulation_id, file_uuid
        )
        print(f" 이미지 저장: {time.time()-t:.2f}초")
        
        # 3. workflow 경로 수정
        t = time.time()
        workflow = get_workflow(input_dir, output_dir, file_uuid)
        print(f"[TIME] workflow 로드: {time.time()-t:.2f}초")

        # ✅ WebSocket 먼저 연결
        t = time.time()
        ws = websocket.WebSocket()
        ws.connect("ws://127.0.0.1:8188/ws?clientId=serverless_worker")
        print(f"[TIME] WebSocket 연결: {time.time()-t:.2f}초")
        
        # 연결 안정화 대기 (짧게)
        time.sleep(0.3)
        
        # 4. ComfyUI 실행
        t = time.time()
        result = queue_prompt(workflow)
        prompt_id = result["prompt_id"]
        print(f"[TIME] ComfyUI 큐 전송: {time.time()-t:.2f}초")

        t = time.time()
        if not wait_for_completion(prompt_id, ws):  # ✅ ws 전달
            return {"error": "Timeout"}
        print(f"[TIME] ComfyUI 추론 완료: {time.time()-t:.2f}초")

        # ✅ ComfyUI 파일 저장 완료 대기 (완료 신호 후 실제 저장까지 약간의 딜레이 있음)
        time.sleep(1)
        
        # ✅ base64 반환 완료!
        t = time.time()
        result_base64 = load_image_as_base64(save_image_path)
        print("[7] base64 변환 완료")    
        print(f"[TIME] base64 변환: {time.time()-t:.2f}초")

        if image_id and conn:
            t = time.time()
            db_update(conn, image_id, image_statement=2, image_url=save_image_path)
            print(f"[TIME] DB UPDATE: {time.time()-t:.2f}초")

        print(f"[TIME] 전체 소요시간: {time.time()-job_start:.2f}초")

        # 6. 결과 반환
        return {
            "status": "success",
            "customer_id": customer_id,
            "simulation_id": simulation_id,
            "uuid": file_uuid,
            "image_id": image_id,
            "save_image_path": save_image_path,
            "image_base64": result_base64,     # base64 인코딩된 PNG 데이터
            "image_media_type": "image/png"  # 미디어 타입
        }


    except Exception as e:
        traceback.print_exc()
        if image_id and conn:
            try:
                db_update(conn, image_id, image_statement=3)
            except:
                pass
        return {"error": str(e)}
        
    finally:
        if conn:
            conn.close()


# ── RunPod 시작점 ──────────────────────────
if __name__ == "__main__":
    # 1. 먼저 ComfyUI가 완전히 뜰 때까지 기다립니다.
    wait_for_comfyui()
    print("[RunPod] ComfyUI 준비 완료. 워커를 시작합니다.")
    runpod.serverless.start({"handler": handler})
