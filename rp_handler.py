import os
import time
import requests
import json
import runpod
import traceback

import websocket # comfyui  완료 감지
import requests
from requests.adapters import HTTPAdapter, Retry

# 이건 왜 필요하지
import urllib.request  # ComfyUI API 호출


BASE_URI = 'http://127.0.0.1:3000'
COMFYUI_URL = 'http://127.0.0.1:8188'
RUNPOD_VOLUME_PATH = '/runpod-volume'
VOLUME_MOUNT_PATH  = os.environ.get("RUNPOD_VOLUME_PATH", "/runpod-volume")
NETWORK_VOLUME = '/workspace'
TIMEOUT = 600

session = requests.Session()
retries = Retry(total=10, backoff_factor=0.1, status_forcelist=[502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))
print("start worker")


# ---------------------------------------------------------------------------- #
#                               ComfyUI Functions                              #
# ---------------------------------------------------------------------------- #

# 이미지 넣을 폴더 생성

def make_job_dirs_and_download(image_url, customer_id, simulation_id, uuid, image_index):
    """
    1. /tmp/input/{customer_id}/{simulation_id}/{uuid}/{image_index}/ 폴더 생성
    2. image_url 다운로드 → {image_index}.png 로 저장 (확장자 무관하게 png로 통일)
    3. output 폴더 생성
    """
    # 입력 폴더 (로컬 tmp, 휘발성)
    input_dir = f"/tmp/input/{customer_id}/{simulation_id}/{uuid}/{image_index}"
    os.makedirs(input_dir, exist_ok=True)

    # 이미지 다운로드
    response = requests.get(image_url, timeout=30) # image_url에 HTTP GET 요청, 30초 안에 응답없으면 에러
    response.raise_for_status() # HTTP 상태코드 확인 200(정상), 404, 500 (에러)

    # 파일명은 항상 {image_index}.png
    filename = f"{image_index}.png"
    input_image_path = os.path.join(input_dir, filename) # input_image 경로

    with open(input_image_path, "wb") as f:
        f.write(response.content)

    # 출력 폴더 (Network Volume, 영구)
    output_dir = f"{VOLUME_MOUNT_PATH}/runpod-slim/ComfyUI/output/{customer_id}/{simulation_id}/{uuid}"
    os.makedirs(output_dir, exist_ok=True)

    save_image_path = os.path.join(output_dir, filename)

    return input_dir, output_dir, input_image_path, save_image_path



#  workflow 수정
def get_workflow(input_dir, output_dir, image_index):
    """
    qwen_model_1229_Fair_blending_websocket_0402_del_segment.json
    input_dir 및 output_dir 경로 현재 job 경로로 교체
    """
    workflow_path = f"{VOLUME_MOUNT_PATH}/runpod-slim/ComfyUI/user/default/workflows/api_qwen_model_1229_Fair_blending_websocket_0402_del_segment.json"
    
    if not os.path.exists(workflow_path):
        raise FileNotFoundError(f"workflow 파일 없음: {workflow_path}")
    
    with open(workflow_path, "r") as f:
        workflow = json.load(f)

    # 노드 23: 입력 경로 교체
    # "D:\\Desktop\\..." → "/tmp/{folder}/input"
    workflow["23"]["inputs"]["path"] = input_dir

    # 노드 51: 출력 경로 교체
    # "D:\\Desktop\\...\\result2" → "/runpod-volume/results/{folder}"
    workflow["51"]["inputs"]["output_path"] = output_dir
    
    # 파일명에 index 추가 (GPU 여러 개가 같은 폴더에 저장할 때 충돌 방지)
    workflow["51"]["inputs"]["filename_prefix"] = f"{image_index}"

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


def wait_for_completion(prompt_id, timeout=600):
    """
    WebSocket으로 ComfyUI 실행 완료 감지
    node: null 이 오면 해당 prompt 완료
    """
    ws = websocket.WebSocket()
    ws.connect(f"ws://127.0.0.1:8188/ws?clientId=serverless_worker")

    start_time = time.time()
    try:
        while time.time() - start_time < timeout:
            msg = ws.recv()
            if isinstance(msg, str):
                data = json.loads(msg)
                if data.get("type") == "executing":
                    node = data["data"].get("node")
                    if node is None and data["data"].get("prompt_id") == prompt_id:
                        return True
    finally:
        ws.close()
    return False




# 메인 handler
def handler(job):
    """
    RunPod이 job 수신 시 자동으로 이 함수를 호출
    job = {
        "id": "job_xxx",
        "input": {
            "image_url": "https://...",
            "customer_id": "cust_001",
            "simulation_id": "sim_042",
            "uuid": "20250416",  # 공통 폴더명용
            "image_index": 0
        }
    }
    """
    job_input = job.get("input", {})

    # ── 입력값 파싱 ──────────────────
    image_url     = job_input.get("image_url")
    customer_id   = job_input.get("customer_id")
    simulation_id = job_input.get("simulation_id")
    uuid = job_input.get("uuid")
    image_index   = job_input.get("image_index", 0)

    # ⭐ image_url은 꼭 있어야 됨
    if not image_url:
        return {"error": "image_url 필요"}

    if not all([customer_id, simulation_id, uuid]):
        return {"error": "customer_id, simulation_id, uuid 필요"}

    try:
        # 1. 디렉토리 생성
        input_dir, output_dir, input_image_path, save_image_path = make_job_dirs_and_download(
            image_url, customer_id, simulation_id, uuid, image_index
        )

        # 3. workflow 경로 수정
        workflow = get_workflow(input_dir, output_dir, image_index)

        # 4. ComfyUI 실행
        result = queue_prompt(workflow)
        prompt_id = result["prompt_id"]

        # 5. 완료 대기
        if not wait_for_completion(prompt_id):
            return {"error": "Timeout"}

        # 6. 결과 반환
        return {
            "status": "success",
            "customer_id": customer_id,
            "simulation_id": simulation_id,
            "uuid": uuid,
            "image_index": image_index,
            "output_dir": output_dir,
            "save_image_path": save_image_path
        }

    except Exception as e:
        return {"error": str(e)}


# ── RunPod 시작점 ──────────────────────────
if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})





# # 1. 백그라운드에서 ComfyUI 서버 실행
# def start_comfyui():
#     subprocess.Popen([
#     "python3",
#     "/workspace/runpod-slim/ComfyUI/main.py",
#     "--listen", "127.0.0.1",
#     "--port", "8188"
# ])
#     # 서버 ready 체크
#     for _ in range(60):
#         try:
#             requests.get("http://127.0.0.1:8188")
#             print("ComfyUI ready")
#             return
#         except:
#             time.sleep(2)


# # 완료 될때까지 대기 함수
# def wait_for_completion(prompt_id):
#     history_url = f"http://127.0.0.1:8188/history/{prompt_id}"

#     while True:
#         res = requests.get(history_url)
#         if res.status_code != 200:
#             time.sleep(1)
#             continue

#         res = res.json()

#         if prompt_id in res:
#             print("ComfyUI 작업 완료")
#             return True

#         time.sleep(1)
