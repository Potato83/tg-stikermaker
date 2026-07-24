# Telegram Sticker Maker

Локальный CLI-инструмент для качественной конвертации GIF, Animated WebP,
APNG и видео в анимированные Telegram-стикеры WebM VP9. Конвертер анализирует
каждый кадр, строит общий bounding box движущегося объекта, сохраняет всю
анимацию, выполняет настоящий двухпроходный encode и бинарным поиском подбирает
максимальное качество без превышения лимита файла.

## Возможности

- GIF, Animated WebP, APNG (`.apng` и `.png`), MP4, MOV, MKV, AVI и WebM;
- ровный холст 512×512, 30 FPS, VP9, `yuva420p`, без звука;
- сохранение прозрачности и прозрачный padding;
- автоматическое ускорение роликов длиннее трёх секунд без обрезки таймлайна;
- потоковый анализ всех кадров: расход памяти не растёт с длительностью файла;
- smart crop по alpha-каналу, а для непрозрачных файлов — по содержимому;
- общий bounding box гарантирует сохранность объекта во всех кадрах;
- Lanczos scaling, аккуратный unsharp и профили `fast`, `balanced`, `best`;
- настоящий libvpx VP9 two-pass для каждой проверяемой пары bitrate/CRF;
- бинарный поиск результата, ближайшего к 256 KiB снизу;
- параллельная обработка нескольких файлов через multiprocessing;
- content-addressed cache с учётом байтов исходника и всех настроек;
- отдельные команды `verify` и `preview`;
- типизированная, модульная архитектура и pytest-тесты.

## Системные требования

- Linux;
- Python 3.11 или новее;
- FFmpeg и ffprobe;
- сборка FFmpeg с encoder `libvpx-vp9`.

Проверить encoder:

```bash
ffmpeg -hide_banner -encoders | grep libvpx-vp9
```

### Установка FFmpeg

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg
```

Arch Linux:

```bash
sudo pacman -S ffmpeg
```

Fedora:

```bash
sudo dnf install ffmpeg
```

## Установка проекта

Вариант с запуском из исходников:

```bash
cd telegram-sticker-maker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Вариант с установкой команды `sticker-maker`:

```bash
python -m pip install .
sticker-maker --help
```

Для разработки:

```bash
python -m pip install -e '.[dev]'
```

## Быстрый старт

Положите исходники в `gifs/`, затем выполните:

```bash
python convert.py
```

Файлы появятся в `output/` с теми же базовыми именами:

```text
gifs/cat.gif      → output/cat.webm
gifs/hello.webp   → output/hello.webm
gifs/dance.mp4    → output/dance.webm
```

Можно явно указать каталоги:

```bash
python convert.py gifs output
```

Эквивалентная явная форма:

```bash
python convert.py convert gifs output
```

После установки пакета:

```bash
sticker-maker convert gifs output
```

## Команда convert

```text
sticker-maker convert [INPUT] [OUTPUT] [options]
```

`INPUT` может быть каталогом или одним поддерживаемым файлом. При обработке
каталога файлы верхнего уровня сортируются по имени. Повреждённый файл отмечается
как ошибка, но не останавливает остальные задания.

Основные параметры:

| Параметр | Назначение |
|---|---|
| `--quality fast\|balanced\|best` | Профиль скорости и качества |
| `--zoom auto\|fit` | Smart crop или сохранение полного кадра |
| `--padding RATIO` | Дополнительный отступ вокруг общего bounding box |
| `--threads N` | Потоки FFmpeg на один worker; `0` означает auto |
| `--workers auto\|N` | Количество одновременно обрабатываемых файлов |
| `--max-size KIB` | Переопределить лимит размера |
| `--overwrite` | Перезаписать существующие результаты |
| `--no-cache` | Не читать и не записывать cache |
| `--dry-run` | Только probe и анализ всех кадров |
| `--config FILE` | Использовать заданный TOML-файл |
| `--cache-dir DIR` | Изменить каталог cache |
| `--verbose` | Расширенные диагностические сообщения |

Примеры:

```bash
python convert.py gifs output --threads 16
python convert.py gifs output --workers auto --quality best
python convert.py gifs output --zoom auto --padding 0.02
python convert.py gifs output --overwrite --no-cache
python convert.py gifs output --verbose
python convert.py gifs output --dry-run
python convert.py one-animation.apng output
```

`--threads` задаёт потоки на процесс, а `--workers` — число процессов. В режиме
auto конвертер делит доступные CPU между worker-процессами, чтобы не создавать
сильную переподписку ядер.

## Команда verify

Проверить один файл:

```bash
python convert.py verify output/cat.webm
```

Проверить все `.webm` в каталоге:

```bash
python convert.py verify output
```

Проверяются:

- codec VP9;
- точные размеры 512×512;
- 30 FPS;
- длительность не больше 3 секунд;
- размер не больше 256 KiB;
- отсутствие audio stream;
- WebM alpha metadata;
- реальные прозрачные пиксели в декодированном RGBA-кадре.

Успешный файл получает статус `✔ OK`. Каждое отдельное нарушение выводится
собственной строкой; команда возвращает ненулевой exit code, если найден хотя
бы один несовместимый файл.

## Команда preview

Контактный лист из двенадцати равномерно распределённых кадров:

```bash
python convert.py preview output/cat.webm
python convert.py preview output/cat.webm cat-sheet.png --frames 16
```

GIF-превью с оптимизированной палитрой и прозрачностью:

```bash
python convert.py preview output/cat.webm --format gif
python convert.py preview output previews --format gif --fps 15
```

При передаче каталога preview создаётся для каждого WebM. Существующие файлы не
перезаписываются без `--overwrite`.

## Конфигурация

По умолчанию CLI ищет `sticker-maker.toml` в текущем каталоге. Другой путь можно
передать через `--config` или переменную окружения `STICKER_MAKER_CONFIG`.

```toml
[platform]
name = "telegram"
width = 512
height = 512
fps = 30.0
max_duration = 3.0
max_size_kib = 256
codec = "libvpx-vp9"
pixel_format = "yuva420p"

[crop]
zoom = "auto"
padding_ratio = 0.015
fill_ratio = 0.97
alpha_threshold = 8
background_threshold = 24.0

[encoding]
quality = "best"
min_bitrate_kbps = 24
max_bitrate_kbps = 5000
search_iterations = 11
size_tolerance_bytes = 768
unsharp_amount = 0.35

[runtime]
workers = "auto"
threads = 0
overwrite = false
cache_enabled = true
verbose = false
```

CLI-параметры переопределяют соответствующие значения TOML. Cache key включает
весь итоговый config, поэтому результат с неподходящими настройками повторно не
используется.

## Профили качества

| Профиль | libvpx `cpu-used` | Поиск | Применение |
|---|---:|---:|---|
| `fast` | 5 | до 6 шагов | быстрые итерации и черновая проверка |
| `balanced` | 3 | до 8 шагов | хороший повседневный компромисс |
| `best` | 1 | до 11 шагов | финальная подготовка стикеров |

Все три профиля используют настоящий two-pass. Профиль меняет скорость libvpx,
число шагов бинарного поиска и последовательность CRF-кандидатов, но не отключает
проверку требований Telegram.

## Как работает pipeline

1. `ffprobe` читает контейнер, video stream, длительность, FPS и audio streams.
2. FFmpeg потоково декодирует **все** исходные кадры в RGBA.
3. Для прозрачной анимации объединяются bounding box всех пикселей с alpha выше
   порога. Для непрозрачной анимации строится адаптивная модель фона по границам
   каждого кадра и объединяется content mask.
4. К общему bounding box добавляется padding. Поэтому рука, волосы или любой
   другой элемент, появляющийся только в одном кадре, остаётся внутри результата.
5. Временная шкала длинного исходника сжимается через `setpts`; `trim` не
   используется. Далее формируется ровно 30 FPS.
6. Область масштабируется Lanczos на прозрачный квадрат 512×512, сохраняя
   пропорции. После scaling применяется мягкий unsharp.
7. Оптимизатор выполняет matching pass 1/pass 2 libvpx VP9, измеряет фактические
   байты WebM и бинарно ищет ближайший результат, не превышающий лимит.
8. Готовый файл проходит независимый `verify`. Некорректный результат удаляется.
9. Валидный WebM атомарно переносится в `output/` и при необходимости в cache.

## Архитектура

| Модуль | Ответственность |
|---|---|
| `cli.py` | argparse, multiprocessing, tqdm, команды и exit codes |
| `config.py` | dataclass-конфигурация, TOML, профили и CLI overrides |
| `models.py` | неизменяемые типизированные доменные модели |
| `ffmpeg.py` | безопасный subprocess boundary, probe/decode/encode/preview |
| `detector.py` | нормализация metadata и all-frame foreground union |
| `cropper.py` | crop/scale/pad/timing filter graph |
| `optimizer.py` | измеряемый бинарный bitrate/CRF search |
| `encoder.py` | orchestration, cache, atomic output и verification |
| `utils.py` | discovery, hashing, пути и CPU allocation |
| `logger.py` | цветные сообщения, совместимые с tqdm |

Ограничения платформы отделены в `PlatformSpec`. Для добавления Discord,
WhatsApp или Line создайте другой spec/config и при необходимости адаптер вывода;
object detection, crop plan и оптимизатор переписывать не требуется.

## Cache

Cache является content-addressed. Ключ зависит от:

- SHA-256 полного исходного файла;
- версии pipeline;
- всех platform, crop, encoding и runtime-настроек.

Перед использованием cached WebM снова проходит `verify`. Битый или устаревший
cached файл автоматически удаляется и пересоздаётся. Временные pass logs и
кандидаты находятся в уникальном временном каталоге и удаляются даже после
ошибки или `Ctrl+C`.

## Тесты

```bash
pytest
pytest --cov=sticker_maker --cov-report=term-missing
ruff check .
ruff format --check .
```

Integration-тест требует FFmpeg с `libvpx-vp9`; при отсутствии encoder он
автоматически пропускается.

## FAQ

### Почему конвертация выполняет много encode-проходов?

VP9 не гарантирует точное число байт по одному bitrate. Каждый кандидат поэтому
кодируется настоящими pass 1 и pass 2, затем измеряется готовый WebM. Это дороже,
но позволяет использовать почти весь разрешённый размер и никогда не доверять
приблизительной формуле.

### Почему итог иногда заметно меньше 256 KiB?

Простая плоская анимация может быть visually lossless уже при существенно
меньшем размере; libvpx не добавляет бессмысленные данные только ради заполнения
лимита. Для сложного материала поиск обычно подходит к лимиту намного ближе.

### Что происходит с роликом длиннее трёх секунд?

Весь таймлайн ускоряется. Кадры с конца не обрезаются. Скорость указывается в
логе, например `2.034×`.

### Почему непрозрачное видео не всегда сильно приближается?

Если фон неоднородный, движется или занимает почти весь кадр, content detector
консервативно выбирает полный кадр. Это безопаснее, чем обрезать часть стикера.
Для такого материала можно предварительно удалить фон либо оставить `--zoom fit`.

### Можно ли отключить auto zoom?

Да:

```bash
python convert.py gifs output --zoom fit
```

### Почему существующий output пропущен?

Защита от случайной перезаписи включена по умолчанию. Используйте `--overwrite`.

### FFmpeg найден, но `libvpx-vp9` отсутствует

Установлена урезанная сборка FFmpeg. Нужна сборка с `--enable-libvpx`. CLI
проверяет это до запуска worker-процессов и выводит понятную ошибку.

### Telegram отклоняет WebM, хотя размер правильный

Сначала выполните `verify`. Помимо размера Telegram требует правильный codec,
геометрию, длительность и отсутствие аудио. Некоторые сторонние декодеры не
показывают VP9 alpha; verifier явно использует decoder `libvpx-vp9`.

## Exit codes

| Код | Значение |
|---:|---|
| `0` | все запрошенные операции успешны либо безопасно пропущены |
| `1` | один или несколько файлов не обработаны/не прошли verify |
| `2` | ошибка аргументов, конфигурации или системной зависимости |
