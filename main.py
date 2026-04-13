import io
import wave
import os
import uuid
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from piper.voice import PiperVoice


app = FastAPI()

MODELS_DIR = "models"
OUTPUTS_DIR = "outputs"
# Читаем модель по умолчанию из окружения
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "ru_RU-irina-medium")

os.makedirs(OUTPUTS_DIR, exist_ok=True)

loaded_voices = {}

# Замените функцию и событие в main.py
def get_voice(model_name: str):
    if model_name not in loaded_voices:
        # Убираем лишние расширения, если они придут в model_name
        base_name = model_name.replace(".onnx", "")
        model_path = os.path.join(MODELS_DIR, f"{base_name}.onnx")
        config_path = os.path.join(MODELS_DIR, f"{base_name}.onnx.json")
        
        if not os.path.exists(model_path):
            print(f"ERROR: File not found {model_path}") # Увидим в логах докера
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found")
            
        loaded_voices[model_name] = PiperVoice.load(model_path, config_path)
    return loaded_voices[model_name]

@app.get("/generate")
async def generate(
    text: str, 
    model: str = Query(None),
    speed: float = 1.0,  # 1.0 - норма, 0.8 - быстро, 1.2 - медленно
    noise: float = 0.667 # Вариативность (интонация)
):
    target_model = model or DEFAULT_MODEL
    voice = get_voice(target_model)
    
    file_id = f"{uuid.uuid4()}.wav"
    file_path = os.path.join(OUTPUTS_DIR, file_id)
    
    with wave.open(file_path, "wb") as wav_file:
        voice.synthesize(
            text, 
            wav_file,
            length_scale=speed, # Управление скоростью
            noise_scale=noise   # Управление "эмоциональным шумом"
        )
    
    return FileResponse(path=file_path, media_type="audio/wav")

@app.on_event("startup")
async def startup_event():
    try:
        get_voice(DEFAULT_MODEL)
        print(f"--- Voice {DEFAULT_MODEL} preloaded successfully ---")
    except Exception as e:
        print(f"--- CRITICAL: Failed to preload model: {e} ---")

