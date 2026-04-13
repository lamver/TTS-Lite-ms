#!/bin/bash
MODEL_DIR="/app/piper_models"
# Один список для всего
#LANGS="ru en es de fr it pt pl uk tr"
LANGS="ru en es "

echo "Проверка моделей для языков: $LANGS"

# Передаем список языков внутрь Python через переменную окружения
export DOWNLOAD_LANGS="$LANGS"

python3 -u -c "
import os, sys
from huggingface_hub import snapshot_download

model_dir = '$MODEL_DIR'
langs = os.getenv('DOWNLOAD_LANGS', '').split()

# Если папки уже есть, snapshot_download просто мгновенно это проверит
# Но чтобы он не лез в сеть лишний раз, если все папки на месте:
if all(os.path.isdir(os.path.join(model_dir, l)) for l in langs):
    print('--- Все папки на месте, сервер готов ---')
    sys.exit(0)

print('--- Обнаружены отсутствующие модели. Начинаю загрузку ---')
patterns = [f'{l}/*' for l in langs]

try:
    snapshot_download(
        repo_id='rhasspy/piper-voices',
        allow_patterns=patterns,
        local_dir=model_dir,
        token=False
    )
    print('\n--- Загрузка завершена успешно ---')
except Exception as e:
    print(f'\n--- Ошибка загрузки: {e} ---', file=sys.stderr)
    sys.exit(1)
"

exec "$@"