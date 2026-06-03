#!/bin/bash
# Расшифровщик документов v2 — надёжная обработка путей с кириллицей и iCloud

SCRIPT="$HOME/Documents/anonymizer/anonymizer.py"

# Проверки
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 не найден. Установи с python.org"
  read -p "Нажми Enter..."
  exit 1
fi

if [ ! -f "$SCRIPT" ]; then
  echo "❌ Скрипт не найден: $SCRIPT"
  echo "   Положи anonymizer.py в ~/Documents/anonymizer/"
  read -p "Нажми Enter..."
  exit 1
fi

# Выбор обезличенных файлов через AppleScript
# Используем linefeed как разделитель (безопасно для путей с пробелами и кириллицей)
FILES=$(osascript -e '
set chosen to choose file with prompt "Выберите обезличенные файлы для расшифровки:" with multiple selections allowed
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

# Парсим пути (разделены переводом строки)
OLDIFS="$IFS"
IFS=$'\n'
FILE_LIST=()
for f in $FILES; do
  [ -n "$f" ] && FILE_LIST+=("$f")
done
IFS="$OLDIFS"

if [ ${#FILE_LIST[@]} -eq 0 ]; then
  echo "❌ Не удалось прочитать выбранные файлы."
  read -p "Нажми Enter..."
  exit 1
fi

echo "Выбрано файлов: ${#FILE_LIST[@]}"
for f in "${FILE_LIST[@]}"; do
  echo "  → $(basename "$f")"
done

# Автопоиск файла ключей
FIRST="${FILE_LIST[0]}"
FOLDER="$(dirname "$FIRST")"

# Ищем самый свежий файл ключей
AUTO_KEYS=""
while IFS= read -r -d '' candidate; do
  AUTO_KEYS="$candidate"
  break
done < <(find "$FOLDER" -maxdepth 1 -name "ключи_*.json" -print0 2>/dev/null | sort -rz)

KEYS=""

if [ -n "$AUTO_KEYS" ]; then
  KEYS_NAME="$(basename "$AUTO_KEYS")"
  echo ""
  echo "Найден файл ключей: $KEYS_NAME"

  CHOICE=$(osascript -e "
    display dialog \"Найден файл ключей:\" & return & return & \"$KEYS_NAME\" & return & return & \"Использовать его?\" buttons {\"Выбрать другой\", \"Использовать\"} default button \"Использовать\"
    return button returned of result
  " 2>/dev/null)

  if [ "$CHOICE" = "Использовать" ]; then
    KEYS="$AUTO_KEYS"
  fi
fi

# Ручной выбор если нужно
if [ -z "$KEYS" ]; then
  KEYS=$(osascript -e "
    try
      set startFolder to POSIX file \"$FOLDER\" as alias
      set k to choose file with prompt \"Выберите файл ключей:\" default location startFolder
      return POSIX path of k
    on error
      return \"\"
    end try
  " 2>/dev/null)
fi

if [ -z "$KEYS" ]; then
  echo "❌ Файл ключей не выбран."
  read -p "Нажми Enter..."
  exit 0
fi

echo "Ключи: $(basename "$KEYS")"
echo ""

# Запуск расшифровки
python3 "$SCRIPT" --restore "${FILE_LIST[@]}" "$KEYS"

echo ""
read -p "Нажми Enter для закрытия..."
