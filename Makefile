PYTHON := python3
VENV_DIR := .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

.PHONY: help venv install transcribe translate burn all clean

help:
	@printf "Targets:\n"
	@printf "  make venv                         Create .venv\n"
	@printf "  make install                      Install Python dependencies into .venv\n"
	@printf "  make transcribe VIDEO=video.mp4   Create audio.wav and raw.srt\n"
	@printf "  make translate SRT=raw.srt        Create translated.srt\n"
	@printf "  make burn VIDEO=video.mp4 SRT=translated.srt  Create output.mp4\n"
	@printf "  make all VIDEO=video.mp4          Run the full pipeline\n"
	@printf "\nOptional vars:\n"
	@printf "  OUT=./out\n"
	@printf "  FONT=Sarabun\n"
	@printf "  START=00:00:00\n"
	@printf "  DURATION=300\n"
	@printf "  LLM_BASE_URL=...\n"
	@printf "  LLM_API_KEY=...\n"
	@printf "  LLM_MODEL=gemini-2.0-flash\n"

venv:
	$(PYTHON) -m venv $(VENV_DIR)

install: venv
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -r requirements.txt

transcribe:
	@test -n "$(VIDEO)" || (echo "Usage: make transcribe VIDEO=/path/to/video.mp4 [OUT=./out]" >&2; exit 1)
	$(VENV_PYTHON) subtitle_pipeline.py transcribe "$(VIDEO)" $(if $(OUT),--output-dir "$(OUT)",) $(if $(START),--start-time "$(START)",) $(if $(DURATION),--duration "$(DURATION)",)

translate:
	@test -n "$(SRT)" || (echo "Usage: make translate SRT=/path/to/raw.srt [OUT=./out]" >&2; exit 1)
	@test -n "$(LLM_BASE_URL)" || (echo "Missing LLM_BASE_URL" >&2; exit 1)
	@test -n "$(LLM_API_KEY)" || (echo "Missing LLM_API_KEY" >&2; exit 1)
	@test -n "$(LLM_MODEL)" || (echo "Missing LLM_MODEL" >&2; exit 1)
	LLM_BASE_URL="$(LLM_BASE_URL)" LLM_API_KEY="$(LLM_API_KEY)" LLM_MODEL="$(LLM_MODEL)" \
	$(VENV_PYTHON) subtitle_pipeline.py translate "$(SRT)" $(if $(OUT),--output-dir "$(OUT)",)

burn:
	@test -n "$(VIDEO)" || (echo "Usage: make burn VIDEO=/path/to/video.mp4 SRT=/path/to/translated.srt [OUT=./out] [FONT=Sarabun]" >&2; exit 1)
	@test -n "$(SRT)" || (echo "Usage: make burn VIDEO=/path/to/video.mp4 SRT=/path/to/translated.srt [OUT=./out] [FONT=Sarabun]" >&2; exit 1)
	$(VENV_PYTHON) subtitle_pipeline.py burn "$(VIDEO)" "$(SRT)" $(if $(OUT),--output-dir "$(OUT)",) $(if $(FONT),--font-name "$(FONT)",)

all:
	@test -n "$(VIDEO)" || (echo "Usage: make all VIDEO=/path/to/video.mp4 [OUT=./out] [FONT=Sarabun]" >&2; exit 1)
	@test -n "$(LLM_BASE_URL)" || (echo "Missing LLM_BASE_URL" >&2; exit 1)
	@test -n "$(LLM_API_KEY)" || (echo "Missing LLM_API_KEY" >&2; exit 1)
	@test -n "$(LLM_MODEL)" || (echo "Missing LLM_MODEL" >&2; exit 1)
	LLM_BASE_URL="$(LLM_BASE_URL)" LLM_API_KEY="$(LLM_API_KEY)" LLM_MODEL="$(LLM_MODEL)" \
	$(VENV_PYTHON) subtitle_pipeline.py all "$(VIDEO)" $(if $(OUT),--output-dir "$(OUT)",) $(if $(FONT),--font-name "$(FONT)",) $(if $(START),--start-time "$(START)",) $(if $(DURATION),--duration "$(DURATION)",)

clean:
	rm -rf __pycache__
