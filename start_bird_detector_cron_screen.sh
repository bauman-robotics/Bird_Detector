#!/bin/bash
# Bird Detector Auto-Start Script: Cron + Screen
# Альтернатива systemd для стабильной производительности GPU приложений

# Настройки (измените под вашу систему)
# === КОНФИГУРАЦИЯ (НАСТРОЙТЕ ПОД СВОЮ СИСТЕМУ) ===
VENV_PATH="/home/pi/projects/Hailo8_projects/Hailo-8/16__hailort_v4.23.0/hailo_runtime_env"
PROJECT_DIR="/home/pi/projects/Hailo8_projects/Hailo-8/17_Bird_Detector"
SESSION_NAME="bird_detector"
LOG_FILE="/home/pi/bird_detector_cron.log"
# === КОНФИГУРАЦИЯ ЗАКОНЧЕНА ===

# Функция логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Начало выполнения
log "=== Запуск Bird Detector через Cron + Screen ==="

# Проверка наличия screen
if ! command -v screen &> /dev/null; then
    log "❌ Screen не установлен. Установка..."
    sudo apt update && sudo apt install -y screen
    if [ $? -ne 0 ]; then
        log "❌ Ошибка установки screen"
        exit 1
    fi
    log "✅ Screen установлен"
fi

# Проверка, не запущен ли уже Bird Detector
if screen -list | grep -q "$SESSION_NAME"; then
    log "⚠️ Bird Detector уже запущен в screen сессии '$SESSION_NAME'"
    screen -list | grep "$SESSION_NAME"
    exit 1
fi

# Проверка наличия виртуального окружения
if [ ! -d "$VENV_PATH" ]; then
    log "❌ Виртуальное окружение не найдено: $VENV_PATH"
    exit 1
fi

# Проверка наличия директории проекта
if [ ! -d "$PROJECT_DIR" ]; then
    log "❌ Директория проекта не найдена: $PROJECT_DIR"
    exit 1
fi

# Проверка наличия скрипта запуска
START_SCRIPT="$PROJECT_DIR/start_bird_detector.sh"
if [ ! -x "$START_SCRIPT" ]; then
    log "❌ Скрипт запуска не найден или не исполняемый: $START_SCRIPT"
    exit 1
fi

# Установка переменных окружения для GPU доступа
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0
export PYTHONPATH=/usr/local/lib/python3.13/dist-packages:$PYTHONPATH

log "✅ Переменные окружения установлены:"
log "   DISPLAY=$DISPLAY"
log "   XAUTHORITY=$XAUTHORITY"
log "   GST_PLUGIN_PATH=$GST_PLUGIN_PATH"

# Запуск Bird Detector в screen сессии
log "🚀 Запуск Bird Detector в screen сессии '$SESSION_NAME'..."

screen -dmS "$SESSION_NAME" bash -c "
    # Логирование в screen сессии
    echo '=== Bird Detector Screen Session Started ==='
    echo \"Started at: \$(date)\"
    echo \"User: \$(whoami)\"
    echo \"PID: \$\$\"

    # Переход в директорию проекта
    cd '$PROJECT_DIR' || exit 1
    echo \"Working directory: \$(pwd)\"

    # Активация виртуального окружения
    source '$VENV_PATH/bin/activate' || exit 1
    echo \"Virtual environment activated: $VENV_PATH\"

    # Повторная установка переменных окружения (на всякий случай)
    export DISPLAY=:0
    export XAUTHORITY=/home/pi/.Xauthority
    export XDG_RUNTIME_DIR=/run/user/1000
    export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0

    echo \"Environment variables set\"
    echo \"DISPLAY=\$DISPLAY\"
    echo \"Starting Bird Detector...\"

    # Запуск Bird Detector
    ./start_bird_detector.sh

    # Если скрипт завершился, логируем
    echo \"Bird Detector exited with code: \$?\"
    echo \"Exit time: \$(date)\"
"

# Проверка успешности запуска
sleep 2
if screen -list | grep -q "$SESSION_NAME"; then
    log "✅ Bird Detector успешно запущен в screen сессии '$SESSION_NAME'"
    log "📋 Информация о сессии:"
    screen -list | grep "$SESSION_NAME" | tee -a "$LOG_FILE"

    log "💡 Для подключения к сессии используйте: screen -r $SESSION_NAME"
    log "💡 Для отключения: Ctrl+A, D"
    log "💡 Для завершения: Ctrl+A, K"

    # Проверка работы веб-стримов через 10 секунд
    (
        sleep 10
        log "🔍 Проверка работы веб-стримов..."
        if curl -s --max-time 5 http://localhost:8080 > /dev/null 2>&1; then
            log "✅ Camera stream доступен: http://localhost:8080"
        else
            log "❌ Camera stream недоступен"
        fi

        if curl -s --max-time 5 http://localhost:8091 > /dev/null 2>&1; then
            log "✅ Detection stream доступен: http://localhost:8091"
        else
            log "❌ Detection stream недоступен"
        fi
    ) &
else
    log "❌ Ошибка запуска Bird Detector в screen сессии"
    exit 1
fi

log "=== Автозапуск завершен успешно ==="
exit 0
