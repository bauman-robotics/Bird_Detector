#!/bin/bash

# Bird Detector Startup Script v5.5
# Активирует виртуальное окружение и запускает Bird Detector

# Настройки (измените под свою систему)
VENV_PATH="/home/pi/projects/Hailo8_projects/Hailo-8/16__hailort_v4.23.0/hailo_runtime_env"  # Путь к виртуальному окружению
PROJECT_DIR="/home/pi/projects/Hailo8_projects/Hailo-8/17_Bird_Detector"  # Путь к проекту
SCRIPT_NAME="bird_detector_v5_5.py"  # Имя основного скрипта

# Функция логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a ./bird_detector_startup.log
}

# Начало выполнения
log "Запуск Bird Detector..."

# Переход в директорию проекта
if cd "$PROJECT_DIR"; then
    log "Перешли в директорию проекта: $PROJECT_DIR"
else
    log "ОШИБКА: Не удалось перейти в директорию $PROJECT_DIR"
    exit 1
fi

# Активация виртуального окружения
if source "$VENV_PATH/bin/activate"; then
    log "Виртуальное окружение активировано: $VENV_PATH"
else
    log "ОШИБКА: Не удалось активировать виртуальное окружение $VENV_PATH"
    exit 1
fi

# Проверка наличия Python скрипта
if [ ! -f "$SCRIPT_NAME" ]; then
    log "ОШИБКА: Скрипт $SCRIPT_NAME не найден в $PROJECT_DIR"
    exit 1
fi

# Проверка наличия конфигурационного файла
if [ ! -f "bird_counter_config_v5.yaml" ]; then
    log "ОШИБКА: Конфигурационный файл bird_counter_config_v5.yaml не найден"
    exit 1
fi

# Настройка переменных окружения для доступа к дисплею (важно для systemd)
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
export XDG_RUNTIME_DIR=/run/user/1000

# Настройка GPU/OpenGL переменных для Raspberry Pi
export MESA_GL_VERSION_OVERRIDE=3.3
export LIBGL_ALWAYS_SOFTWARE=0

# Дополнительные переменные для производительности
export V4L2_DRIVER=v4l2
export GST_V4L2_USE_LIBV4L2=1
export GST_DEBUG=0
export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0
export PYTHONPATH=/usr/local/lib/python3.11/dist-packages:$PYTHONPATH

# Переменные для уменьшения задержки видео
export SDL_VIDEODRIVER=wayland
export QT_QPA_PLATFORM=wayland
export WAYLAND_DISPLAY=wayland-0

log "Переменные окружения установлены:"
log "  DISPLAY=$DISPLAY"
log "  XAUTHORITY=$XAUTHORITY"
log "  XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"

# Проверка и создание файла X authority
if [ ! -f "$XAUTHORITY" ]; then
    log "Создание файла X authority..."
    touch "$XAUTHORITY"
    chmod 600 "$XAUTHORITY"
fi

# Запуск Bird Detector с явной установкой DISPLAY
log "Запуск $SCRIPT_NAME с DISPLAY=:0..."
DISPLAY=:0 python3 "$SCRIPT_NAME" --input rpi 2>&1 | tee -a ./bird_detector_runtime.log

# Код ниже выполнится только при ошибке
log "ОШИБКА: Bird Detector завершился с кодом $?"
exit 1
