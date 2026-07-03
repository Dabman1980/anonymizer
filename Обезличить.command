#!/bin/bash
# Обезличиватель документов v2 — надёжная обработка путей с кириллицей и iCloud

SCRIPT_DIR="$HOME/ClaudeProjects/real/anonymizer"
SCRIPT="$SCRIPT_DIR/anonymizer.py"

# Проверки
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 не найден. Установи с python.org"
  read -p "Нажми Enter..."
  exit 1
fi

if [ ! -f "$SCRIPT" ]; then
  echo "❌ Скрипт не найден: $SCRIPT"
  echo "   Положи anonymizer.py в ~/ClaudeProjects/real/anonymizer/"
  read -p "Нажми Enter..."
  exit 1
fi

# Установка зависимостей при первом запуске
DEPS_FLAG="$SCRIPT_DIR/.deps_installed"
if [ ! -f "$DEPS_FLAG" ]; then
  echo "Устанавливаю зависимости (один раз)..."
  pip3 install python-docx openpyxl pdfplumber --break-system-packages -q
  touch "$DEPS_FLAG"
fi

# Выбор файлов через AppleScript с linefeed-разделителем
FILES=$(osascript -e '
set chosen to choose file with prompt "Выберите файлы для обезличивания:" with multiple selections allowed
set output to ""
repeat with f in chosen
  set output to output & POSIX path of f & linefeed
end repeat
return output
' 2>/dev/null)

if [ -z "$FILES" ]; then
  echo "Файлы не выбраны."
  read -p "Нажми Enter..."
  exit 0
fi

# Парсим пути
OLDIFS="$IFS"
IFS=$'\n'
FILE_LIST=()
for f in $FILES; do
  [ -n "$f" ] && FILE_LIST+=("$f")
done
IFS="$OLDIFS"

echo "Выбрано файлов: ${#FILE_LIST[@]}"
for f in "${FILE_LIST[@]}"; do
  echo "  → $(basename "$f")"
done
echo ""

python3 "$SCRIPT" "${FILE_LIST[@]}"

echo ""
read -p "Нажми Enter для закрытия..."
