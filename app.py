import os
import json
import uuid
import time
from flask import Flask, render_template, request, Response, stream_with_context, jsonify, session, redirect, url_for, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import openai
import requests
import httpx
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# === Configuration ===
BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
DATA_DIR = "data"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

FILES = {
    "users": os.path.join(DATA_DIR, "users.json"),
    "chats": os.path.join(DATA_DIR, "chats.json")
}

# === Helper Functions ===
def load_data(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

http_client = httpx.Client(
    proxy="socks5h://127.0.0.1:9150",
    # transport=httpx.HTTPTransport(local_address="0.0.0.0"), # 가끔 필요한 설정(선택)
)

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

# === Routes: Auth ===
# app.py의 index 함수 수정
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # 사용자 이름 포맷팅 로직
    username = session['user_id']
    # 공백(단어 구분)이 없는 경우: 소문자로 변환 후 첫 글자만 대문자로
    if ' ' not in username:
        username = username.lower().capitalize()
        
    return render_template('index.html', username=username)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.json.get('username')
        password = request.json.get('password')
        users = load_data(FILES['users'])
        if username in users and check_password_hash(users[username]['password'], password):
            session['user_id'] = username
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Invalid credentials"}), 401
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.json.get('username')
        password = request.json.get('password')
        users = load_data(FILES['users'])
        if username in users:
            return jsonify({"success": False, "message": "User already exists"}), 400
        users[username] = {"password": generate_password_hash(password)}
        save_data(FILES['users'], users)
        return jsonify({"success": True})
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# === Routes: Data & Models ===
@app.route('/models', methods=['GET'])
def get_models():
    try:
        headers = {"Authorization": f"Bearer {API_KEY}"}
        response = requests.get(f"{BASE_URL}/models", headers=headers)
        if response.status_code == 200:
            return jsonify(response.json())
        return jsonify({"error": "Failed", "data": []}), 500
    except Exception as e:
        return jsonify({"error": str(e), "data": []}), 500

# === Routes: Chat Logic ===
@app.route('/history', methods=['GET'])
def get_history():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    chats = load_data(FILES['chats'])
    user_chats = chats.get(session['user_id'], [])
    user_chats.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
    return jsonify(user_chats)

@app.route('/history/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    chats = load_data(FILES['chats'])
    user_id = session['user_id']
    if user_id in chats:
        chats[user_id] = [c for c in chats[user_id] if c['id'] != chat_id]
        save_data(FILES['chats'], chats)
        return jsonify({"success": True})
    return jsonify({"error": "Chat not found"}), 404

@app.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    messages = data.get('messages', [])
    model_name = data.get('model', 'gpt-4o')
    
    # 프론트엔드에서 ID가 없으면(새 채팅) 여기서 생성
    chat_id = data.get('chat_id')
    if not chat_id:
        chat_id = str(uuid.uuid4())

    user_id = session['user_id']

    def generate():
        full_response = ""
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=True
            )

            for chunk in response:
                if chunk.choices:
                    content = chunk.choices[0].delta.content
                    if content:
                        full_response += content
                        yield content
            
            # === 저장 로직 ===
            all_chats = load_data(FILES['chats'])
            if user_id not in all_chats:
                all_chats[user_id] = []
            
            current_timestamp = time.time()
            
            # 해당 ID의 채팅이 이미 있는지 확인
            existing_chat = next((c for c in all_chats[user_id] if c['id'] == chat_id), None)
            
            if existing_chat:
                # 기존 채팅 업데이트 (내용 덮어쓰기)
                existing_chat['messages'] = messages + [{"role": "assistant", "content": full_response}]
                existing_chat['timestamp'] = current_timestamp
            else:
                # 새 채팅 생성
                first_msg = next((m['content'] for m in messages if m['role'] == 'user'), "New Chat")
                title = first_msg[:25] + "..." if len(first_msg) > 25 else first_msg
                
                new_chat_entry = {
                    "id": chat_id,
                    "title": title,
                    "timestamp": current_timestamp,
                    "messages": messages + [{"role": "assistant", "content": full_response}]
                }
                all_chats[user_id].append(new_chat_entry)
            
            save_data(FILES['chats'], all_chats)

        except Exception as e:
            yield f"Error: {str(e)}"

    # Response 객체를 만들고 헤더에 Chat ID 포함
    response = Response(stream_with_context(generate()), mimetype='text/plain')
    response.headers['X-Chat-ID'] = chat_id
    return response

@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    # 캐시 문제 방지를 위해 헤더 설정을 해주면 더 좋습니다 (선택사항)
    response.headers['Cache-Control'] = 'no-cache' 
    return response

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')