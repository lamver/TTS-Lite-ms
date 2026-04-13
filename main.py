import os
import uuid
import wave
import json
import glob
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from piper.voice import PiperVoice

app = FastAPI(title="Piper TTS Microservice")

# Конфигурация из окружения
MODELS_DIR = "piper_models"
OUTPUTS_DIR = "outputs"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "ru_RU-denis-medium")

# Создаем папку для аудио, если её нет
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Глобальные переменные для хранения реестра и загруженных моделей
models_registry = {}
loaded_voices = {}

def scan_models():
    """Рекурсивно сканирует MODELS_DIR и собирает данные о моделях и спикерах"""
    registry = {}
    # Ищем все файлы конфигурации .onnx.json
    config_files = glob.glob(os.path.join(MODELS_DIR, "**", "*.onnx.json"), recursive=True)
    
    for config_path in config_files:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            
            model_path = config_path.replace(".json", "")
            if not os.path.exists(model_path):
                continue
                
            base_dir = os.path.dirname(config_path)
            model_id = os.path.basename(model_path).replace(".onnx", "")
            
            # Извлекаем спикеров
            speakers_map = config_data.get("speaker_id_map", {})
            
            # Ищем MODEL_CARD
            card_path = os.path.join(base_dir, "MODEL_CARD")
            model_card = ""
            if os.path.exists(card_path):
                with open(card_path, "r", encoding="utf-8") as f:
                    model_card = f.read()
            
            # Ищем примеры голосов (сэмплы)
            samples_dir = os.path.join(base_dir, "samples")
            samples = []
            if os.path.exists(samples_dir):
                samples = [s for s in os.listdir(samples_dir) if s.endswith(".mp3")]

            registry[model_id] = {
                "id": model_id,
                "path": model_path,
                "config_path": config_path,
                "speakers": list(speakers_map.keys()),
                "speaker_ids": speakers_map,
                "card": model_card,
                "samples": samples,
                "sample_rate": config_data.get("audio", {}).get("sample_rate", 22050)
            }
        except Exception as e:
            print(f"Error scanning model at {config_path}: {e}")
            
    return registry

@app.on_event("startup")
async def startup_event():
    """Срабатывает при запуске контейнера"""
    global models_registry
    print(f"--- Scanning directory: {MODELS_DIR} ---")
    models_registry = scan_models()
    print(f"--- Found {len(models_registry)} models ---")
    
    # Предзагрузка дефолтной модели
    if DEFAULT_MODEL in models_registry:
        try:
            m = models_registry[DEFAULT_MODEL]
            loaded_voices[DEFAULT_MODEL] = PiperVoice.load(m["path"], m["config_path"])
            print(f"--- Default model '{DEFAULT_MODEL}' is ready ---")
        except Exception as e:
            print(f"--- Failed to preload default model: {e} ---")

@app.get("/models")
async def get_models():
    """Возвращает список всех найденных моделей и их спикеров"""
    return JSONResponse(content=models_registry)

@app.get("/generate")
async def generate(
    text: str, 
    model: str = Query(None),
    speaker: str = Query(None),
    speed: float = 1.0, 
    noise: float = 0.667 
):
    """Генерирует аудиофайл"""
    target_id = model or DEFAULT_MODEL
    
    if target_id not in models_registry:
        raise HTTPException(status_code=404, detail=f"Model '{target_id}' not found. Check /models for list.")
    
    m_info = models_registry[target_id]
    
    # Загружаем в память, если еще не загружена
    if target_id not in loaded_voices:
        try:
            loaded_voices[target_id] = PiperVoice.load(m_info["path"], m_info["config_path"])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error loading model: {e}")

    voice = loaded_voices[target_id]
    
    # Определяем ID спикера (по имени или по номеру)
    speaker_id = None
    if speaker:
        if m_info["speaker_ids"] and speaker in m_info["speaker_ids"]:
            speaker_id = m_info["speaker_ids"][speaker]
        elif speaker.isdigit():
            speaker_id = int(speaker)

    file_id = f"{uuid.uuid4()}.wav"
    file_path = os.path.join(OUTPUTS_DIR, file_id)
    
    # Синтез
    try:
        with wave.open(file_path, "wb") as wav_file:
            voice.synthesize(
                text, 
                wav_file, 
                length_scale=speed, 
                noise_scale=noise,
                speaker_id=speaker_id
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")
    
    return FileResponse(path=file_path, media_type="audio/wav", filename=f"{target_id}_{file_id}")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)