#!/bin/bash

# Check if a directory argument is provided
if [ -z "$1" ]; then
  echo "Usage: ./launch_me.sh <directory_path>"
  exit 1
fi

TARGET_DIR="$1"

# Check if the target directory exists
if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory '$TARGET_DIR' not found."
  exit 1
fi

echo "Launching AI in directory: $TARGET_DIR"

# Change to the target directory
cd "$TARGET_DIR" || exit 1

# Launch the AI (main.py lives next to this script, not in the target dir)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python "$SCRIPT_DIR/main.py"
