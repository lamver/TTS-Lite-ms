import io
import wave
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from piper.voice import PiperVoice

app = FastAPI()

MODELS_DIR = "models"
# Читаем модель по умолчанию из окружения
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "ru_RU-irina-medium")

loaded_voices = {}

def get_voice(model_name: str):
    if model_name not in loaded_voices:
        model_path = os.path.join(MODELS_DIR, f"{model_name}.onnx")
        config_path = os.path.join(MODELS_DIR, f"{model_name}.onnx.json")
        
        if not os.path.exists(model_path):
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found")
            
        # Загружаем модель в память воркера
        loaded_voices[model_name] = PiperVoice.load(model_path, config_path)
    return loaded_voices[model_name]

@app.get("/generate")
async def generate(
    text: str, 
    model: str = Query(None) # Если не указано, возьмем DEFAULT_MODEL
):
    target_model = model or DEFAULT_MODEL
    voice = get_voice(target_model)
    
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize(text, wav_file)
    
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="audio/wav")

# Прогрев модели при старте воркера
@app.on_event("startup")
async def startup_event():
    get_voice(DEFAULT_MODEL)
    print(f"Worker started and preloaded: {DEFAULT_MODEL}")
