import asyncio
import aio_pika
import os
import uuid
import wave
import json
import glob
import aioboto3
from fastapi import FastAPI, HTTPException, Query, Response, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from piper.voice import PiperVoice

app = FastAPI(title="Piper TTS Microservice")

RABBIT_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
IN_QUEUE = os.getenv("QUEUE_NAME", "text_to_voice_lite_piter")
OUT_QUEUE = os.getenv("QUEUE_NAME", "text_to_voice_lite_piter_result")

# Настройки S3 из окружения
S3_CONFIG = {
    "endpoint_url": os.getenv("S3_ENDPOINT"),
    "aws_access_key_id": os.getenv("S3_ACCESS_KEY"),
    "aws_secret_access_key": os.getenv("S3_SECRET_KEY"),
}
BUCKET_NAME = os.getenv("S3_BUCKET")

async def upload_to_s3(local_path, s3_key):
    """Загружает файл в S3 и возвращает путь (key)"""
    session = aioboto3.Session()
    async with session.client("s3", **S3_CONFIG) as s3:
        with open(local_path, "rb") as f:
            await s3.put_object(
                Bucket=BUCKET_NAME,
                Key=s3_key,
                Body=f,
                ContentType="audio/wav"
            )
    return s3_key


async def send_result(channel, request_id, success: bool):
    try:
        payload = json.dumps({"requestId": request_id, "status": success}).encode()
        await channel.default_exchange.publish(
            aio_pika.Message(body=payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=OUT_QUEUE
        )
        print(f"--- РЕЗУЛЬТАТ ОТПРАВЛЕН: {request_id} (status: {success}) ---", flush=True)
    except Exception as e:
        print(f"--- ОШИБКА ОТПРАВКИ РЕЗУЛЬТАТА: {e} ---", flush=True)

async def send_error_result(channel, request_id, error_message):
    """Отправляет отчет об ошибке в результирующую очередь"""
    payload = json.dumps({
        "requestId": request_id,
        "status": False,
        "error": error_message
    }).encode()
    
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=payload, 
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        ),
        routing_key=OUT_QUEUE
    )
    
async def process_mq_tasks():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBIT_URL)
            async with connection:
                channel = await connection.channel()
                in_queue = await channel.declare_queue(IN_QUEUE, durable=True)
                await channel.declare_queue(OUT_QUEUE, durable=True)
                await channel.set_qos(prefetch_count=1)

                async with in_queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        request_id = "unknown"
                        try:
                            data = json.loads(message.body.decode())
                            request_id = data.get("requestId")
                            text = data.get("text")
                            
                            print(f"--- [ВЗЯЛ]: {request_id} ---", flush=True)
                            dynamic_out_queue = data.get("out_queue", OUT_QUEUE) 

                            # 1. Синтез
                            local_file = await run_synthesis(request_id, text, data.get("model"), data.get("speaker"))
                            
                            if local_file is None:
                                raise ValueError("run_synthesis returned None. Check return statement!")

                            # 2. Проверка S3 конфига перед загрузкой
                            if not all([os.getenv("S3_ENDPOINT"), os.getenv("S3_BUCKET")]):
                                raise ValueError("S3 Config is missing in ENV variables!")

                            s3_path = f"WORKERS/{IN_QUEUE}/{request_id}/{request_id}.wav"
                            short_text = text[:100].replace("\n", " ") 
                            # 3. Загрузка
                            await upload_to_s3(local_file, s3_path, metadata={"text": short_text})
                            
                            # 3. УДАЛЕНИЕ СРАЗУ ПОСЛЕ S3
                            if os.path.exists(local_file):
                                os.remove(local_file)
                                print(f"--- [ФАЙЛ УДАЛЕН ПОСЛЕ S3]: {local_file} ---", flush=True)

                            # 4. Результат
                            res_data = {"requestId": request_id, "status": True, "path": s3_path}
                            await channel.default_exchange.publish(
                                aio_pika.Message(body=json.dumps(res_data).encode()),
                                routing_key=dynamic_out_queue
                            )

                            if os.path.exists(local_file): os.remove(local_file)
                            await message.ack()
                            print(f"--- [УСПЕХ]: {request_id} ---", flush=True)

                        except Exception as e:
                            print(f"--- [ОШИБКА]: {request_id} | {e} ---", flush=True)
                            # Отправляем ошибку в очередь результатов
                            await send_error_result(channel, request_id, str(e))
                            await message.ack()
        except Exception as e:
            print(f"--- [КРИТИЧЕСКАЯ ОШИБКА MQ]: {e} ---", flush=True)
            await asyncio.sleep(10)
            
async def run_synthesis(request_id, text, model_id, speaker):
    # (Логика остается прежней: загрузка модели + synthesize в asyncio.to_thread)
    if model_id not in loaded_voices:
        m_info = models_registry.get(model_id)
        if not m_info: raise Exception(f"Model {model_id} not found")
        loaded_voices[model_id] = PiperVoice.load(m_info["path"], m_info["config_path"])

    voice = loaded_voices[model_id]
    m_info = models_registry[model_id]
    
    speaker_id = None
    if speaker:
        if m_info["speaker_ids"] and speaker in m_info["speaker_ids"]:
            speaker_id = m_info["speaker_ids"][speaker]
        elif str(speaker).isdigit():
            speaker_id = int(speaker)

    file_path = os.path.join(OUTPUTS_DIR, f"{request_id}.wav")
    
    def _sync_synth():
        with wave.open(file_path, "wb") as wav_file:
            voice.synthesize(text, wav_file, speaker_id=speaker_id)

    await asyncio.to_thread(_sync_synth)
    
    return file_path
    
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
    print("!!!!!!!!!! SERVER IS STARTING NOW !!!!!!!!!!!", flush=True)
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
            
    asyncio.create_task(process_mq_tasks())

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

@app.get("/get-audio/{filename}")
async def get_audio(filename: str, background_tasks: BackgroundTasks):
    """Отдает WAV файл и удаляет его после скачивания"""
    # Безопасно формируем путь к файлу
    file_path = os.path.join(OUTPUTS_DIR, filename)
    
    # Проверяем, существует ли он и не пытаются ли нас взломать через ../
    if not os.path.exists(file_path) or ".." in filename:
        raise HTTPException(status_code=404, detail="File not found")

    # Добавляем задачу на удаление файла ПОСЛЕ отправки ответа
    background_tasks.add_task(os.remove, file_path)
    
    return FileResponse(
        path=file_path, 
        media_type="audio/wav", 
        filename=filename
    )

@app.get("/sample/{model_id}/{speaker_name}")
async def get_speaker_sample(model_id: str, speaker_name: str):
    """Возвращает пример голоса конкретного спикера (файл .mp3 из папки samples)"""
    if model_id not in models_registry:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    model_info = models_registry[model_id]
    samples_dir = os.path.join(os.path.dirname(model_info["config_path"]), "samples")
    
    # Проверяем наличие подпапки samples
    if not os.path.exists(samples_dir):
        raise HTTPException(status_code=404, detail=f"No samples directory for model '{model_id}'")
    
    # Поиск файлов, связанных с указанным спикером (например, speaker_name.mp3 или speaker_name_*.mp3)
    possible_files = [
        f for f in os.listdir(samples_dir) 
        if f.endswith(".mp3") and speaker_name in f
    ]
    
    if not possible_files:
        raise HTTPException(
            status_code=404, 
            detail=f"No sample found for speaker '{speaker_name}' in model '{model_id}'"
        )
    
    # Выбираем первый подходящий файл
    sample_file = possible_files[0]
    sample_path = os.path.join(samples_dir, sample_file)
    
    # Проверяем существование файла
    if not os.path.exists(sample_path):
        raise HTTPException(status_code=404, detail="Sample file not accessible")
    
    return FileResponse(
        path=sample_path,
        media_type="audio/mpeg",
        filename=sample_file
    )
    
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)
