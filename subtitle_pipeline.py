#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Sequence

SYSTEM_PROMPT = """You are a professional subtitle translator specializing in Korean-to-Thai livestream subtitles.
Translate the subtitle text below from Korean to Thai.

Context:
- This is a casual Korean livestream / streaming VOD
- The primary speaker is a 27-year-old female streamer
- The subtitles may contain casual speech, chat reactions, game terms, slang, hesitation, filler words, and STT errors
- The goal is Thai subtitles that feel natural, easy to read, and faithful to what was actually said

Rules:
- Preserve every SRT block exactly: index number, timestamp line, and blank line separator must remain unchanged
- Only translate subtitle text lines; never change index numbers or timestamps
- Translate into natural Thai suitable for on-screen subtitles
- Keep the tone conversational, like a real streamer talking naturally on stream
- Reflect Korean politeness and speech level naturally in Thai, but do not overuse Thai particles
- When the speaker clearly refers to herself, use natural feminine Thai phrasing where appropriate
- Do not invent missing meaning; if the Korean is unclear, garbled, or obviously affected by STT errors, translate conservatively based on the most likely meaning
- If a word is too unclear to confidently interpret, keep it short and neutral rather than guessing wildly
- Keep names, nicknames, game terms, item names, and proper nouns consistent throughout the file
- Do not transliterate Korean words into Thai unless they are names or proper nouns
- Preserve repeated lines only if they are truly repeated in the source text
- Remove obvious filler or STT noise only when doing so does not change the meaning
- Keep each subtitle short and natural for reading on screen, ideally no more than about 40 Thai characters per line
- If a line is a greeting to chat or viewers, translate it like a natural Thai streamer greeting

Return only the translated SRT content. No explanation, no markdown, no code block.
"""

TEXT_TRANSLATION_SYSTEM_PROMPT = """You are a professional subtitle translator specializing in Korean-to-Thai livestream subtitles.
Translate Korean subtitle text into natural Thai.

Context:
- This is a casual Korean livestream / streaming VOD
- The primary speaker is a 27-year-old female streamer
- The subtitles may contain casual speech, chat reactions, game terms, slang, hesitation, filler words, and STT errors
- The goal is Thai subtitles that feel natural, easy to read, and faithful to what was actually said

Rules:
- Translate only the subtitle text for each block
- Keep the number of output blocks exactly the same as the input blocks
- Keep names, nicknames, game terms, item names, and proper nouns consistent throughout the file
- Do not invent missing meaning; if the Korean is unclear, translate conservatively
- Keep each subtitle short and natural for reading on screen
- Return only the requested block markers and Thai translations
- Do not add explanations, code fences, timestamps, or extra blocks
"""

DEFAULT_FONT_CANDIDATES = [
    "Sarabun",
    "TH Sarabun New",
    "Noto Sans Thai",
    "Noto Serif Thai",
    "Garuda",
    "Loma",
    "Norasi",
    "Kinnari",
    "Waree",
]

TIMESTAMP_LINE_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3}) --> (?P<end>\d{2}:\d{2}:\d{2},\d{3})$"
)
FILLER_WORDS = {
    "아",
    "어",
    "어어",
    "아아",
    "음",
    "음음",
    "응",
    "으",
    "어?",
    "음?",
    "어...",
    "음...",
}
NORMALIZED_FILLER_WORDS = {re.sub(r"[^\w가-힣]+", "", word.casefold()) for word in FILLER_WORDS}


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class SRTBlock:
    index: str
    timestamp: str
    text_lines: list[str]


def load_dotenv(dotenv_path: Path | None = None) -> None:
    path = dotenv_path or Path(".env")
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if value:
            try:
                parsed = shlex.split(value, posix=True)
                value = parsed[0] if len(parsed) == 1 else value
            except ValueError:
                pass

        os.environ[key] = value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    command_names = {"transcribe", "translate", "burn", "all"}
    normalized_argv = list(argv if argv is not None else sys.argv[1:])
    if normalized_argv and not normalized_argv[0].startswith("-") and normalized_argv[0] not in command_names:
        normalized_argv = ["all", *normalized_argv]

    parser = argparse.ArgumentParser(
        description="Generate Korean->Thai burned-in subtitles for a video.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Extract audio and transcribe Korean speech into an SRT named after the source video",
    )
    transcribe_parser.add_argument("video_path", help="Path to the input .mp4 video")
    add_output_dir_arg(transcribe_parser)
    add_clip_args(transcribe_parser)

    translate_parser = subparsers.add_parser(
        "translate",
        help="Translate an existing SRT file from Korean to Thai",
    )
    translate_parser.add_argument("input_srt", help="Path to the source SRT file")
    add_llm_args(translate_parser)
    add_output_dir_arg(translate_parser)

    burn_parser = subparsers.add_parser(
        "burn",
        help="Burn an SRT subtitle file into a video",
    )
    burn_parser.add_argument("video_path", help="Path to the input .mp4 video")
    burn_parser.add_argument("subtitle_path", help="Path to the subtitle .srt file")
    add_output_dir_arg(burn_parser)
    add_font_arg(burn_parser)

    all_parser = subparsers.add_parser(
        "all",
        help="Run transcribe, translate, and burn-in in one go",
    )
    all_parser.add_argument("video_path", help="Path to the input .mp4 video")
    add_llm_args(all_parser)
    add_output_dir_arg(all_parser)
    add_font_arg(all_parser)
    add_clip_args(all_parser)

    return parser.parse_args(normalized_argv)


def add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("LLM_BASE_URL"),
        help="Base URL for the OpenAI-compatible API (defaults to LLM_BASE_URL)",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.getenv("LLM_API_KEY"),
        help="API key for the OpenAI-compatible API (defaults to LLM_API_KEY)",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_MODEL"),
        help="Model name for translation (defaults to LLM_MODEL)",
    )


def add_output_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Base directory for output files (default: current directory)",
    )


def add_font_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--font-name",
        default=os.getenv("SUBTITLE_FONT"),
        help="Preferred subtitle font family name",
    )


def add_clip_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start-time",
        default="0",
        help="Clip start time in seconds or HH:MM:SS[.mmm] format (default: 0)",
    )
    parser.add_argument(
        "--duration",
        help="Clip duration in seconds or HH:MM:SS[.mmm] format",
    )


def require_module(module_name: str, package_name: str) -> None:
    if find_spec(module_name) is None:
        raise PipelineError(
            f"Missing Python dependency '{package_name}'. "
            "Create a virtualenv and install dependencies with "
            "`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`."
        )


def require_env_or_arg(name: str, value: str | None) -> str:
    if value:
        return value
    raise PipelineError(f"Missing required setting '{name}'. Provide it as a flag or environment variable.")


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", normalized):
        return f"{normalized}/v1"
    return normalized


def resolve_api_key(value: str | None, base_url: str) -> str:
    if value is not None and value != "":
        return value

    if re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(?:/|$)", base_url):
        return "lm-studio"

    raise PipelineError("Missing required setting 'LLM_API_KEY'. Provide it as a flag or environment variable.")


def ensure_tool_exists(tool_name: str) -> None:
    if shutil.which(tool_name) is None:
        raise PipelineError(f"Required CLI tool '{tool_name}' is not installed or not on PATH.")


def run_command(command: Sequence[str], step_name: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "(no stderr output)"
        raise PipelineError(f"{step_name} failed.\n{stderr}")
    return completed


def parse_time_value(value: str | None, label: str) -> float | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        raise PipelineError(f"{label} cannot be empty.")

    if re.fullmatch(r"\d+(\.\d+)?", cleaned):
        seconds = float(cleaned)
    else:
        match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?", cleaned)
        if not match:
            raise PipelineError(
                f"Invalid {label} '{value}'. Use seconds or HH:MM:SS[.mmm] format."
            )
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        secs = int(match.group(3))
        millis_text = match.group(4) or "0"
        millis = int(millis_text.ljust(3, "0"))
        seconds = hours * 3600 + minutes * 60 + secs + millis / 1000

    if seconds < 0:
        raise PipelineError(f"{label} must be greater than or equal to 0.")
    return seconds


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def parse_srt_timestamp(value: str) -> float:
    hours, minutes, seconds_millis = value.split(":")
    seconds, millis = seconds_millis.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def block_times(block: SRTBlock) -> tuple[float, float]:
    match = TIMESTAMP_LINE_RE.fullmatch(block.timestamp)
    if not match:
        raise PipelineError(f"Malformed SRT timestamp line: {block.timestamp}")
    return parse_srt_timestamp(match.group("start")), parse_srt_timestamp(match.group("end"))


def make_block(start_seconds: float, end_seconds: float, text: str) -> SRTBlock:
    return SRTBlock(
        index="",
        timestamp=f"{format_srt_timestamp(start_seconds)} --> {format_srt_timestamp(end_seconds)}",
        text_lines=[normalize_whitespace(text)],
    )


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def block_text(block: SRTBlock) -> str:
    return normalize_whitespace(" ".join(block.text_lines))


def normalize_text_for_matching(text: str) -> str:
    return re.sub(r"[^\w가-힣]+", "", text.casefold())


def is_filler_block(block: SRTBlock) -> bool:
    text = block_text(block)
    if not text:
        return True
    normalized = normalize_text_for_matching(text)
    return normalized in NORMALIZED_FILLER_WORDS


def remove_filler_blocks(blocks: list[SRTBlock]) -> list[SRTBlock]:
    return [block for block in blocks if not is_filler_block(block)]


def remove_repeated_long_runs(blocks: list[SRTBlock]) -> list[SRTBlock]:
    cleaned: list[SRTBlock] = []
    index = 0

    while index < len(blocks):
        current = blocks[index]
        current_key = normalize_text_for_matching(block_text(current))
        run_end = index + 1
        while run_end < len(blocks) and normalize_text_for_matching(block_text(blocks[run_end])) == current_key:
            run_end += 1

        run = blocks[index:run_end]
        durations = [block_times(block)[1] - block_times(block)[0] for block in run]
        total_duration = sum(durations)
        if current_key and len(run) >= 3 and total_duration >= 45 and max(durations, default=0) >= 10:
            index = run_end
            continue

        cleaned.extend(run)
        index = run_end

    return cleaned


def should_merge_blocks(left: SRTBlock, right: SRTBlock) -> bool:
    left_start, left_end = block_times(left)
    right_start, right_end = block_times(right)
    gap = right_start - left_end
    if gap < 0 or gap > 0.35:
        return False

    left_text = block_text(left)
    right_text = block_text(right)
    if not left_text or not right_text:
        return False
    if is_filler_block(left) or is_filler_block(right):
        return False
    if re.search(r"[.!?]$", left_text):
        return False
    if len(left_text) + len(right_text) > 42:
        return False
    if (right_end - left_start) > 8:
        return False
    return len(left_text) <= 18 or len(right_text) <= 18


def merge_short_blocks(blocks: list[SRTBlock]) -> list[SRTBlock]:
    if not blocks:
        return []

    merged: list[SRTBlock] = [blocks[0]]
    for block in blocks[1:]:
        previous = merged[-1]
        if should_merge_blocks(previous, block):
            start_seconds, _ = block_times(previous)
            _, end_seconds = block_times(block)
            merged_text = f"{block_text(previous)} {block_text(block)}"
            merged[-1] = make_block(start_seconds, end_seconds, merged_text)
            continue
        merged.append(block)
    return merged


def clean_transcript_blocks(blocks: list[SRTBlock]) -> list[SRTBlock]:
    cleaned = remove_repeated_long_runs(blocks)
    cleaned = remove_filler_blocks(cleaned)
    cleaned = merge_short_blocks(cleaned)
    return cleaned


def write_srt(blocks: list[SRTBlock], destination: Path) -> None:
    if not blocks:
        raise PipelineError("Whisper produced empty segments. Aborting.")

    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        text_lines = [line.rstrip() for line in block.text_lines if line.strip()]
        if not text_lines:
            continue

        lines.append(str(index))
        lines.append(block.timestamp)
        lines.extend(text_lines)
        lines.append("")

    if not lines:
        raise PipelineError("Whisper produced only empty subtitle text. Aborting.")

    destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def render_srt(blocks: Sequence[SRTBlock]) -> str:
    lines: list[str] = []
    for block in blocks:
        text_lines = [line.rstrip() for line in block.text_lines if line.strip()]
        if not text_lines:
            continue

        lines.append(block.index)
        lines.append(block.timestamp)
        lines.extend(text_lines)
        lines.append("")

    if not lines:
        raise PipelineError("Encountered an empty SRT chunk during rendering.")

    return "\n".join(lines).rstrip() + "\n"


def parse_srt(content: str) -> list[SRTBlock]:
    normalized = content.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    blocks: list[SRTBlock] = []
    for raw_block in re.split(r"\n\s*\n", normalized):
        lines = [line.rstrip("\n") for line in raw_block.split("\n")]
        if len(lines) < 3:
            raise PipelineError("Encountered malformed SRT content during validation.")
        blocks.append(
            SRTBlock(
                index=lines[0].strip(),
                timestamp=lines[1].strip(),
                text_lines=lines[2:],
            )
        )
    return blocks


def extract_audio(
    video_path: Path,
    audio_path: Path,
    *,
    start_time_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> None:
    command = ["ffmpeg", "-y"]
    if start_time_seconds > 0:
        command.extend(["-ss", str(start_time_seconds)])
    command.extend(["-i", str(video_path)])
    if duration_seconds is not None:
        command.extend(["-t", str(duration_seconds)])
    command.extend(
        [
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    run_command(command, "Audio extraction")


def choose_whisper_device() -> tuple[str, bool]:
    requested_device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    if requested_device:
        if requested_device == "cuda":
            return "cuda", True
        if requested_device in {"mps", "cpu"}:
            return requested_device, False
        raise PipelineError("WHISPER_DEVICE must be one of: cpu, mps, cuda.")

    import torch  # type: ignore

    if torch.backends.mps.is_available():
        return "mps", False
    if torch.cuda.is_available():
        return "cuda", True
    return "cpu", False


def get_transcribe_chunk_seconds() -> float:
    raw_value = os.getenv("WHISPER_CHUNK_SECONDS", "900").strip()
    if not raw_value:
        return 900.0
    try:
        chunk_seconds = float(raw_value)
    except ValueError as exc:
        raise PipelineError("WHISPER_CHUNK_SECONDS must be a positive number.") from exc
    if chunk_seconds <= 0:
        raise PipelineError("WHISPER_CHUNK_SECONDS must be greater than 0.")
    return chunk_seconds


def get_transcribe_overlap_seconds() -> float:
    raw_value = os.getenv("WHISPER_CHUNK_OVERLAP_SECONDS", "1.5").strip()
    if not raw_value:
        return 1.5
    try:
        overlap_seconds = float(raw_value)
    except ValueError as exc:
        raise PipelineError("WHISPER_CHUNK_OVERLAP_SECONDS must be a non-negative number.") from exc
    if overlap_seconds < 0:
        raise PipelineError("WHISPER_CHUNK_OVERLAP_SECONDS must be greater than or equal to 0.")
    return overlap_seconds


def get_wav_duration_seconds(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        if frame_rate <= 0:
            raise PipelineError(f"Invalid WAV frame rate in {audio_path.name}.")
        return wav_file.getnframes() / frame_rate


def write_wav_chunk(
    source_audio_path: Path,
    destination_audio_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    with wave.open(str(source_audio_path), "rb") as source_wav:
        frame_rate = source_wav.getframerate()
        if frame_rate <= 0:
            raise PipelineError(f"Invalid WAV frame rate in {source_audio_path.name}.")

        start_frame = max(0, int(start_seconds * frame_rate))
        frame_count = max(0, int(math.ceil(duration_seconds * frame_rate)))
        source_wav.setpos(start_frame)
        frames = source_wav.readframes(frame_count)

        with wave.open(str(destination_audio_path), "wb") as destination_wav:
            destination_wav.setnchannels(source_wav.getnchannels())
            destination_wav.setsampwidth(source_wav.getsampwidth())
            destination_wav.setframerate(frame_rate)
            destination_wav.writeframes(frames)


def block_midpoint_seconds(block: SRTBlock) -> float:
    start_seconds, end_seconds = block_times(block)
    return (start_seconds + end_seconds) / 2


def block_belongs_to_chunk(block: SRTBlock, *, owned_start: float, owned_end: float, is_last_chunk: bool) -> bool:
    midpoint = block_midpoint_seconds(block)
    if is_last_chunk:
        return owned_start <= midpoint <= owned_end
    return owned_start <= midpoint < owned_end


def load_whisper_model() -> tuple[object, bool]:
    require_module("whisper", "openai-whisper")
    import whisper  # type: ignore

    device, use_fp16 = choose_whisper_device()
    precision_label = "fp16" if use_fp16 else "fp32"
    print(f"Using Whisper device: {device} ({precision_label})", file=sys.stderr)
    return whisper.load_model("large-v3", device=device), use_fp16


def transcribe_audio_with_model(
    model: object,
    audio_path: Path,
    *,
    timestamp_offset_seconds: float = 0.0,
    use_fp16: bool,
) -> list[SRTBlock]:
    result = model.transcribe(
        str(audio_path),
        language="ko",
        fp16=use_fp16,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.45,
        compression_ratio_threshold=2.0,
        logprob_threshold=-0.8,
        hallucination_silence_threshold=1.0,
    )
    segments = result.get("segments") or []

    blocks: list[SRTBlock] = []
    for segment in segments:
        text = normalize_whitespace(str(segment.get("text", "")))
        if not text:
            continue

        blocks.append(
            SRTBlock(
                index="",
                timestamp=(
                    f"{format_srt_timestamp(float(segment.get('start', 0.0)) + timestamp_offset_seconds)} --> "
                    f"{format_srt_timestamp(float(segment.get('end', 0.0)) + timestamp_offset_seconds)}"
                ),
                text_lines=[text],
            )
        )

    if not blocks:
        raise PipelineError("Whisper produced empty segments. Aborting.")

    return blocks


def transcribe_audio(audio_path: Path, *, timestamp_offset_seconds: float = 0.0) -> list[SRTBlock]:
    model, use_fp16 = load_whisper_model()
    return transcribe_audio_with_model(
        model,
        audio_path,
        timestamp_offset_seconds=timestamp_offset_seconds,
        use_fp16=use_fp16,
    )


def transcribe_audio_in_chunks(
    audio_path: Path,
    *,
    scratch_dir: Path,
    timestamp_offset_seconds: float = 0.0,
) -> list[SRTBlock]:
    clip_duration_seconds = get_wav_duration_seconds(audio_path)
    chunk_seconds = get_transcribe_chunk_seconds()
    if clip_duration_seconds <= chunk_seconds:
        print_step("Step 2/2: Transcribing Korean audio with Whisper large-v3...")
        return transcribe_audio(audio_path, timestamp_offset_seconds=timestamp_offset_seconds)

    overlap_seconds = min(get_transcribe_overlap_seconds(), chunk_seconds / 4)
    total_chunks = math.ceil(clip_duration_seconds / chunk_seconds)
    print_step(
        f"Step 2/2: Transcribing Korean audio with Whisper large-v3 in {total_chunks} chunks..."
    )

    model, use_fp16 = load_whisper_model()
    combined_blocks: list[SRTBlock] = []

    for chunk_index in range(total_chunks):
        chunk_start_seconds = chunk_index * chunk_seconds
        chunk_end_seconds = min(clip_duration_seconds, chunk_start_seconds + chunk_seconds)
        extract_start_seconds = max(0.0, chunk_start_seconds - overlap_seconds)
        extract_end_seconds = min(clip_duration_seconds, chunk_end_seconds + overlap_seconds)
        extract_duration_seconds = extract_end_seconds - extract_start_seconds
        chunk_audio_path = scratch_dir / f"chunk_{chunk_index + 1:03d}.wav"

        write_wav_chunk(
            audio_path,
            chunk_audio_path,
            start_seconds=extract_start_seconds,
            duration_seconds=extract_duration_seconds,
        )

        print_step(
            f"Step 2/2: Transcribing chunk {chunk_index + 1}/{total_chunks}..."
        )
        chunk_blocks = transcribe_audio_with_model(
            model,
            chunk_audio_path,
            timestamp_offset_seconds=timestamp_offset_seconds + extract_start_seconds,
            use_fp16=use_fp16,
        )

        owned_start_seconds = timestamp_offset_seconds + chunk_start_seconds
        owned_end_seconds = timestamp_offset_seconds + chunk_end_seconds
        is_last_chunk = chunk_index == total_chunks - 1
        combined_blocks.extend(
            block
            for block in chunk_blocks
            if block_belongs_to_chunk(
                block,
                owned_start=owned_start_seconds,
                owned_end=owned_end_seconds,
                is_last_chunk=is_last_chunk,
            )
        )

    if not combined_blocks:
        raise PipelineError("Whisper produced empty segments after chunked transcription. Aborting.")

    return combined_blocks


def extract_message_text(message_content: object) -> str:
    if isinstance(message_content, str):
        return message_content

    if isinstance(message_content, list):
        parts: list[str] = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif getattr(item, "type", None) == "text":
                parts.append(str(getattr(item, "text", "")))
        return "".join(parts).strip()

    return ""


def summarize_response(response: object) -> str:
    try:
        if hasattr(response, "model_dump"):
            data = response.model_dump()
        elif isinstance(response, dict):
            data = response
        else:
            data = {"repr": repr(response)}
    except Exception:
        return repr(response)

    summary_keys = ("id", "object", "model", "choices", "output", "output_text", "error")
    summary = {key: data.get(key) for key in summary_keys if key in data}
    return repr(summary)


def extract_response_text(response: object) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is not None:
            content = extract_message_text(getattr(message, "content", None))
            if content:
                return content

        text = getattr(first_choice, "text", None)
        if isinstance(text, str) and text.strip():
            return text

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content_items = getattr(item, "content", None)
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                text = getattr(content_item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    raise PipelineError(
        "The LLM endpoint returned a response without readable text content. "
        f"Response summary: {summarize_response(response)}"
    )


def validate_translated_srt(raw_content: str, translated_content: str) -> tuple[bool, str]:
    try:
        raw_blocks = parse_srt(raw_content)
        translated_blocks = parse_srt(translated_content)
    except PipelineError as exc:
        return False, str(exc)

    if len(raw_blocks) != len(translated_blocks):
        return False, f"block count mismatch ({len(raw_blocks)} != {len(translated_blocks)})"

    for position, (raw_block, translated_block) in enumerate(zip(raw_blocks, translated_blocks), start=1):
        if raw_block.index != translated_block.index:
            return False, f"index mismatch at block {position}"
        if raw_block.timestamp != translated_block.timestamp:
            return False, f"timestamp mismatch at block {position}"

    return True, "ok"


def chunk_blocks(blocks: Sequence[SRTBlock], chunk_size: int) -> list[list[SRTBlock]]:
    if chunk_size <= 0:
        raise PipelineError("Translation chunk size must be greater than 0.")
    return [list(blocks[index:index + chunk_size]) for index in range(0, len(blocks), chunk_size)]


def get_translate_chunk_size() -> int:
    raw_value = os.getenv("LLM_TRANSLATE_BLOCKS_PER_CHUNK", "60").strip()
    if not raw_value:
        return 60
    if not raw_value.isdigit():
        raise PipelineError("LLM_TRANSLATE_BLOCKS_PER_CHUNK must be a positive integer.")
    chunk_size = int(raw_value)
    if chunk_size <= 0:
        raise PipelineError("LLM_TRANSLATE_BLOCKS_PER_CHUNK must be greater than 0.")
    return chunk_size


def get_translate_context_size() -> int:
    raw_value = os.getenv("LLM_TRANSLATE_CONTEXT_BLOCKS", "4").strip()
    if not raw_value:
        return 4
    if not raw_value.isdigit():
        raise PipelineError("LLM_TRANSLATE_CONTEXT_BLOCKS must be a non-negative integer.")
    return int(raw_value)


def format_block_marker(block_number: int) -> str:
    return f"<<<BLOCK {block_number}>>>"


def build_translation_block_payload(blocks: Sequence[SRTBlock]) -> str:
    lines: list[str] = []
    for block_number, block in enumerate(blocks, start=1):
        lines.append(format_block_marker(block_number))
        lines.extend(line.rstrip() for line in block.text_lines if line.strip())
        lines.append("")

    if not lines:
        raise PipelineError("Encountered an empty translation chunk.")

    return "\n".join(lines).rstrip() + "\n"


def parse_translated_block_payload(content: str, expected_blocks: int) -> list[str]:
    marker_pattern = re.compile(r"^<<<BLOCK (\d+)>>>$", re.MULTILINE)
    matches = list(marker_pattern.finditer(content))
    if not matches:
        raise PipelineError("The translation response did not include any block markers.")

    translated_entries: list[str] = []
    for position, match in enumerate(matches):
        block_number = int(match.group(1))
        expected_number = position + 1
        if block_number != expected_number:
            raise PipelineError(
                f"Expected translation block marker {expected_number}, received {block_number}."
            )

        content_start = match.end()
        content_end = matches[position + 1].start() if position + 1 < len(matches) else len(content)
        translated_text = content[content_start:content_end].strip()
        if not translated_text:
            raise PipelineError(f"Translated block {block_number} is empty.")
        translated_entries.append(translated_text)

    if len(translated_entries) != expected_blocks:
        raise PipelineError(
            f"Translated block count mismatch ({expected_blocks} != {len(translated_entries)})"
        )

    return translated_entries


def build_translation_prompt(
    chunk_blocks: Sequence[SRTBlock],
    *,
    previous_source_blocks: Sequence[SRTBlock],
    previous_translated_blocks: Sequence[SRTBlock],
) -> str:
    sections = [
        "Translate the CURRENT subtitle blocks from Korean to Thai.",
        "Return exactly one translated entry for each CURRENT block.",
        "Use the same block markers exactly as provided.",
        "Do not output timestamps, numbering, explanations, or code fences.",
    ]

    if previous_source_blocks and previous_translated_blocks:
        sections.extend(
            [
                "",
                "Previous source context for consistency only:",
                build_translation_block_payload(previous_source_blocks).rstrip(),
                "",
                "Previous Thai translation for consistency only:",
                build_translation_block_payload(previous_translated_blocks).rstrip(),
                "",
                "Do not repeat the previous context in the answer.",
            ]
        )

    sections.extend(
        [
            "",
            "CURRENT blocks to translate:",
            build_translation_block_payload(chunk_blocks).rstrip(),
        ]
    )
    return "\n".join(sections).strip()


def translate_srt_chunk(
    client: object,
    *,
    model_name: str,
    chunk_blocks: Sequence[SRTBlock],
    chunk_number: int,
    total_chunks: int,
    previous_source_blocks: Sequence[SRTBlock],
    previous_translated_blocks: Sequence[SRTBlock],
) -> list[SRTBlock]:
    last_error = "unknown validation error"
    user_prompt = build_translation_prompt(
        chunk_blocks,
        previous_source_blocks=previous_source_blocks,
        previous_translated_blocks=previous_translated_blocks,
    )

    for attempt in range(1, 3):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": TEXT_TRANSLATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            raise PipelineError(
                f"Translation request failed on chunk {chunk_number}/{total_chunks}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        translated_content = extract_response_text(response).strip()
        if not translated_content:
            last_error = "empty response content"
            continue

        try:
            translated_entries = parse_translated_block_payload(translated_content, len(chunk_blocks))
        except PipelineError as exc:
            last_error = str(exc)
        else:
            translated_blocks: list[SRTBlock] = []
            for raw_block, translated_text in zip(chunk_blocks, translated_entries):
                translated_lines = [line.strip() for line in translated_text.splitlines() if line.strip()]
                if not translated_lines:
                    last_error = f"Translated block {raw_block.index} is empty."
                    break
                translated_blocks.append(
                    SRTBlock(
                        index=raw_block.index,
                        timestamp=raw_block.timestamp,
                        text_lines=translated_lines,
                    )
                )
            else:
                return translated_blocks

        if attempt == 1:
            print(
                f"Chunk {chunk_number}/{total_chunks} validation failed ({last_error}); retrying once...",
                file=sys.stderr,
            )

    raise PipelineError(
        f"Translated SRT validation failed on chunk {chunk_number}/{total_chunks} after retry: {last_error}"
    )


def translate_srt(
    raw_content: str,
    *,
    base_url: str,
    api_key: str,
    model_name: str,
) -> str:
    require_module("openai", "openai")
    from openai import OpenAI  # type: ignore

    client = OpenAI(base_url=base_url, api_key=api_key)
    raw_blocks = parse_srt(raw_content)
    if not raw_blocks:
        raise PipelineError("Input SRT is empty. Aborting.")

    chunk_size = get_translate_chunk_size()
    context_size = get_translate_context_size()
    chunks = chunk_blocks(raw_blocks, chunk_size)
    translated_chunks: list[SRTBlock] = []
    translated_blocks_so_far: list[SRTBlock] = []

    for chunk_index, chunk_blocks_list in enumerate(chunks, start=1):
        if len(chunks) > 1:
            print(
                f"Translating chunk {chunk_index}/{len(chunks)} ({len(chunk_blocks_list)} blocks)...",
                file=sys.stderr,
            )

        previous_source_blocks = raw_blocks[max(0, len(translated_blocks_so_far) - context_size):len(translated_blocks_so_far)]
        previous_translated_blocks = translated_blocks_so_far[-context_size:]
        translated_chunk_blocks = translate_srt_chunk(
            client,
            model_name=model_name,
            chunk_blocks=chunk_blocks_list,
            chunk_number=chunk_index,
            total_chunks=len(chunks),
            previous_source_blocks=previous_source_blocks,
            previous_translated_blocks=previous_translated_blocks,
        )
        translated_chunks.extend(translated_chunk_blocks)
        translated_blocks_so_far.extend(translated_chunk_blocks)

    return render_srt(translated_chunks)


def choose_font(preferred_font: str | None) -> str:
    candidates: list[str] = []
    if preferred_font:
        candidates.append(preferred_font)
    for font_name in DEFAULT_FONT_CANDIDATES:
        if font_name not in candidates:
            candidates.append(font_name)

    fc_list = shutil.which("fc-list")
    if not fc_list:
        return candidates[0]

    completed = subprocess.run(
        [fc_list, ":", "family"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return candidates[0]

    available: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        for family in line.split(","):
            cleaned = family.strip()
            if cleaned:
                available.setdefault(cleaned.lower(), cleaned)

    for candidate in candidates:
        match = available.get(candidate.lower())
        if match:
            return match

    for candidate in candidates:
        lowered = candidate.lower()
        for family_lower, family in available.items():
            if lowered in family_lower:
                return family

    return candidates[0]


def escape_filter_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(",", r"\,")
    )


def burn_subtitles(video_path: Path, subtitle_path: Path, output_path: Path, font_name: str) -> None:
    subtitle_value = escape_filter_value(str(subtitle_path.resolve()))
    style_value = escape_filter_value(
        f"FontName={font_name},FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=1"
    )
    video_filter = f"subtitles=filename='{subtitle_value}':force_style='{style_value}'"

    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-c:a",
            "copy",
            str(output_path),
        ],
        "Subtitle burn-in",
    )


def verify_output_files(paths: Sequence[Path]) -> None:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise PipelineError(f"Expected output files were not created: {', '.join(missing)}")


def verify_playable_video(video_path: Path) -> None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        if video_path.stat().st_size == 0:
            raise PipelineError(f"{video_path.name} was created but is empty.")
        return

    run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        "Output video verification",
    )


def print_step(message: str) -> None:
    print(message, file=sys.stderr)


def require_existing_file(path_str: str, label: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise PipelineError(f"{label} does not exist: {path}")
    return path


def ensure_output_dir(path_str: str) -> Path:
    output_dir = Path(path_str).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def ensure_stage_output_dir(base_dir: Path, stage_name: str) -> Path:
    stage_dir = base_dir / stage_name
    stage_dir.mkdir(parents=True, exist_ok=True)
    return stage_dir


def derived_output_path(output_dir: Path, source_path: Path, suffix: str) -> Path:
    return output_dir / f"{source_path.stem}{suffix}"


def run_transcribe(
    video_path: Path,
    output_dir: Path,
    *,
    start_time_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> tuple[Path, Path, Path]:
    ensure_tool_exists("ffmpeg")
    audio_path = derived_output_path(output_dir, video_path, ".audio.wav")
    raw_whisper_srt_path = derived_output_path(output_dir, video_path, ".raw_whisper.srt")
    raw_srt_path = derived_output_path(output_dir, video_path, ".raw.srt")

    print_step("Step 1/2: Extracting audio...")
    extract_audio(
        video_path,
        audio_path,
        start_time_seconds=start_time_seconds,
        duration_seconds=duration_seconds,
    )

    with tempfile.TemporaryDirectory(prefix="whisper_chunks_", dir=output_dir) as scratch_dir:
        raw_blocks = transcribe_audio_in_chunks(
            audio_path,
            scratch_dir=Path(scratch_dir),
            timestamp_offset_seconds=start_time_seconds,
        )
    write_srt(raw_blocks, raw_whisper_srt_path)

    cleaned_blocks = clean_transcript_blocks(raw_blocks)
    if not cleaned_blocks:
        raise PipelineError("Transcript cleaning removed every subtitle block. Aborting.")

    write_srt(cleaned_blocks, raw_srt_path)
    verify_output_files([audio_path, raw_whisper_srt_path, raw_srt_path])
    return audio_path, raw_whisper_srt_path, raw_srt_path


def run_translate(args: argparse.Namespace, input_srt_path: Path, output_dir: Path) -> Path:
    base_url = normalize_base_url(require_env_or_arg("LLM_BASE_URL", args.llm_base_url))
    api_key = resolve_api_key(args.llm_api_key, base_url)
    model_name = require_env_or_arg("LLM_MODEL", args.llm_model)
    translated_srt_path = derived_output_path(output_dir, input_srt_path, ".translated.srt")

    print_step("Step 1/1: Translating subtitles to Thai...")
    raw_content = input_srt_path.read_text(encoding="utf-8")
    translated_content = translate_srt(
        raw_content,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
    )
    translated_srt_path.write_text(translated_content, encoding="utf-8")
    verify_output_files([translated_srt_path])
    return translated_srt_path


def run_burn(video_path: Path, subtitle_path: Path, output_dir: Path, font_name_arg: str | None) -> Path:
    ensure_tool_exists("ffmpeg")
    output_video_path = derived_output_path(output_dir, video_path, ".subtitled.mp4")

    print_step("Step 1/1: Burning Thai subtitles into the output video...")
    font_name = choose_font(font_name_arg)
    print_step(f"Using subtitle font: {font_name}")
    burn_subtitles(video_path, subtitle_path, output_video_path, font_name)

    verify_output_files([output_video_path])
    verify_playable_video(output_video_path)
    return output_video_path


def main() -> int:
    load_dotenv()
    args = parse_args()
    start_time_seconds = parse_time_value(getattr(args, "start_time", None), "start time") or 0.0
    duration_seconds = parse_time_value(getattr(args, "duration", None), "duration")

    if args.command == "transcribe":
        video_path = require_existing_file(args.video_path, "Input video")
        output_dir = ensure_stage_output_dir(ensure_output_dir(args.output_dir), "transcribe")
        audio_path, raw_whisper_srt_path, raw_srt_path = run_transcribe(
            video_path,
            output_dir,
            start_time_seconds=start_time_seconds,
            duration_seconds=duration_seconds,
        )
        print(f"audio: {audio_path}")
        print(f"raw_whisper: {raw_whisper_srt_path}")
        print(f"raw_srt: {raw_srt_path}")
        return 0

    if args.command == "translate":
        input_srt_path = require_existing_file(args.input_srt, "Input SRT")
        output_dir = ensure_stage_output_dir(ensure_output_dir(args.output_dir), "translate")
        translated_srt_path = run_translate(args, input_srt_path, output_dir)
        print(f"translated_srt: {translated_srt_path}")
        return 0

    if args.command == "burn":
        video_path = require_existing_file(args.video_path, "Input video")
        subtitle_path = require_existing_file(args.subtitle_path, "Subtitle file")
        output_dir = ensure_stage_output_dir(ensure_output_dir(args.output_dir), "burn")
        output_video_path = run_burn(video_path, subtitle_path, output_dir, args.font_name)
        print(f"output_video: {output_video_path}")
        return 0

    video_path = require_existing_file(args.video_path, "Input video")
    base_output_dir = ensure_output_dir(args.output_dir)
    transcribe_output_dir = ensure_stage_output_dir(base_output_dir, "transcribe")
    translate_output_dir = ensure_stage_output_dir(base_output_dir, "translate")
    burn_output_dir = ensure_stage_output_dir(base_output_dir, "burn")
    _, raw_whisper_srt_path, raw_srt_path = run_transcribe(
        video_path,
        transcribe_output_dir,
        start_time_seconds=start_time_seconds,
        duration_seconds=duration_seconds,
    )
    print(f"raw_whisper: {raw_whisper_srt_path}")
    translated_srt_path = run_translate(args, raw_srt_path, translate_output_dir)
    output_video_path = run_burn(video_path, translated_srt_path, burn_output_dir, args.font_name)

    print(f"raw_srt: {raw_srt_path}")
    print(f"translated_srt: {translated_srt_path}")
    print(f"output_video: {output_video_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
