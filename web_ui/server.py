import os
import sys
import json
import asyncio
import uuid
import urllib.parse
import signal
from pathlib import Path
from typing import Callable, Dict, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="STT Video Pipeline UI")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


def load_env_file(path: Path) -> None:
    """Load local configuration without overwriting explicitly exported values."""
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'\""))


load_env_file(BASE_DIR / ".env")

# Ensure directories exist
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(BASE_DIR / "video" / "download", exist_ok=True)
os.makedirs(BASE_DIR / "video" / "burn", exist_ok=True)
os.makedirs(BASE_DIR / "transcribe", exist_ok=True)
os.makedirs(BASE_DIR / "translate", exist_ok=True)

# In-memory job store
jobs: Dict[str, dict] = {}
job_logs: Dict[str, List[str]] = {}
job_processes: Dict[str, asyncio.subprocess.Process] = {}

class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"
    threads: int = 8
    start_offset: str = "00:00:00"
    duration: Optional[str] = None
    output_name: Optional[str] = None

class TranscribeRequest(BaseModel):
    video_path: str
    start_time: Optional[str] = None
    duration: Optional[str] = None
    source_lang: Optional[str] = "ko"
    timing_mode: Optional[str] = "auto"

class PromptOptions(BaseModel):
    topic: Optional[str] = "casual livestream"
    tone: Optional[str] = "conversational and natural"
    speaker: Optional[str] = "neutral"
    custom_rules: Optional[str] = None

class TranslateRequest(BaseModel):
    input_srt: str
    model: Optional[str] = None
    prompt_path: Optional[str] = None
    source_lang: Optional[str] = "ko"
    prompt_options: Optional[PromptOptions] = None

class IntegrateRequest(BaseModel):
    video_path: str
    srt_path: str
    mode: str = "mux"  # "mux" (mkv) or "burn" (mp4)
    font_name: Optional[str] = None

class FullPipelineRequest(BaseModel):
    url: Optional[str] = None
    quality: Optional[str] = "best"
    threads: Optional[int] = 4
    start_offset: Optional[str] = "00:00:00"
    duration: Optional[str] = None
    output_name: Optional[str] = None
    font_name: Optional[str] = None
    source_lang: Optional[str] = "ko"
    video_path: Optional[str] = None
    timing_mode: Optional[str] = "auto"
    prompt_options: Optional[PromptOptions] = None


def is_youtube_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return False
    host = host.lower()
    return host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com")


def is_chzzk_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return False
    return host.lower() in {"chzzk.naver.com", "www.chzzk.naver.com"}


def chzzk_cookie_arguments(url: str) -> List[str]:
    if not is_chzzk_url(url):
        return []

    nid_aut = os.getenv("CHZZK_NID_AUT")
    nid_ses = os.getenv("CHZZK_NID_SES")
    if bool(nid_aut) != bool(nid_ses):
        raise ValueError(
            "CHZZK_NID_AUT and CHZZK_NID_SES must be set together in .env"
        )
    if not nid_aut:
        return []

    return [
        "--http-cookie", f"NID_AUT={nid_aut}",
        "--http-cookie", f"NID_SES={nid_ses}",
    ]


def safe_download_name(value: str) -> str:
    cleaned = "".join(
        "_" if char in '<>:"/\\|?*\0' else char for char in value
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:160]


def redacted_command(cmd: List[str]) -> str:
    sensitive_flags = {"--http-cookie", "--http-header", "--http-cookies-file"}
    redacted: List[str] = []
    redact_next = False
    for part in cmd:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
        else:
            redacted.append(part)
            redact_next = part in sensitive_flags
    return " ".join(redacted)


def status_environment() -> Dict[str, str]:
    visible_keys = {
        "LLM_BASE_URL",
        "LLM_MODEL",
        "SUBTITLE_FONT",
        "WHISPER_DEVICE",
        "WHISPER_MODEL",
        "WHISPER_TEMPERATURES",
        "WHISPER_WORD_TIMESTAMPS",
    }
    return {
        key: os.environ[key]
        for key in visible_keys
        if os.getenv(key) is not None
    }


def youtube_format_selector(quality: str) -> str:
    if quality == "best":
        return "bv+ba/b"
    if quality == "worst":
        return "wv*+wa/w"
    if quality.endswith("p") and quality[:-1].isdigit():
        return f"bv[height<={quality[:-1]}]+ba/b[height<={quality[:-1]}]"
    return "bv+ba/b"


def youtube_download_section(start_offset: str, duration: Optional[str]) -> Optional[str]:
    if not duration:
        return None

    def seconds(value: str) -> float:
        hours, minutes, seconds_value = value.split(":")
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds_value)
        if total < 0:
            raise ValueError("Time values must not be negative")
        return total

    def timestamp(value: float) -> str:
        hours, remainder = divmod(value, 3600)
        minutes, seconds_value = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{seconds_value:06.3f}".rstrip("0").rstrip(".")

    return f"*{start_offset}-{timestamp(seconds(start_offset) + seconds(duration))}"


def build_download_command(
    url: str,
    quality: str,
    threads: int,
    start_offset: str,
    duration: Optional[str],
    output_file: Path,
) -> tuple[List[str], str]:
    if is_youtube_url(url):
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--format",
            youtube_format_selector(quality),
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--concurrent-fragments",
            str(threads),
            "--newline",
            "--output",
            str(output_file),
        ]
        section = youtube_download_section(start_offset, duration)
        if section:
            command.extend(["--download-sections", section])
        command.append(url)
        return command, "yt-dlp"

    command = [
        "streamlink", url, quality,
        "--stream-segment-threads", str(threads),
        "--hls-start-offset", start_offset,
        "--progress", "force",
        *chzzk_cookie_arguments(url),
        "-o", str(output_file),
    ]
    if duration:
        command.extend(["--stream-segmented-duration", duration])
    return command, "Streamlink"

def generate_dynamic_prompt(source_lang: str, options: PromptOptions, job_id: str) -> Path:
    lang_map = {
        "ko": "Korean",
        "zh": "Chinese",
        "ja": "Japanese",
        "en": "English"
    }
    src_lang_name = lang_map.get(source_lang, "Korean")
    
    prompt = f"You are a professional subtitle translator specializing in {src_lang_name}-to-Thai subtitles.\n"
    prompt += f"Translate {src_lang_name} subtitle text into natural Thai.\n\n"
    
    prompt += "Context:\n"
    prompt += f"- This is a {options.topic}.\n"
    prompt += "- The subtitles may contain casual speech, slang, hesitation, filler words, and STT errors.\n"
    prompt += "- The goal is Thai subtitles that feel natural, easy to read, and faithful to what was actually said.\n\n"
    
    prompt += "Tone & Style:\n"
    prompt += f"- The tone should be {options.tone}.\n"
    if options.speaker and options.speaker != "neutral":
        prompt += f"- Speaker profile: {options.speaker}.\n"
    
    prompt += "\nHandling Multiple Speakers:\n"
    prompt += "- **Multiple Speakers**: If the context shows a conversation between multiple people, format the translation to clarify who is speaking. For example, use a hyphen `- ` prefix at the start of a line to indicate a speaker change within a subtitle block.\n\n"
    
    if options.custom_rules:
        prompt += f"Custom Rules:\n- {options.custom_rules.strip()}\n\n"
        
    prompt += "Rules:\n"
    prompt += "- Translate only the subtitle text for each block.\n"
    prompt += "- Keep the number of output blocks exactly the same as the input blocks.\n"
    prompt += "- Keep names, nicknames, game terms, item names, and proper nouns consistent throughout the file.\n"
    prompt += "- Do not invent missing meaning; if the source is unclear, translate conservatively.\n"
    prompt += f"- Do not transliterate {src_lang_name} words into Thai unless they are names or proper nouns.\n"
    prompt += "- Preserve repeated lines only if they are truly repeated in the source text.\n"
    prompt += "- Remove obvious filler or STT noise only when doing so does not change the meaning.\n"
    prompt += "- Keep each subtitle short and natural for reading on screen.\n"
    prompt += "- Return only the requested block markers and Thai translations.\n"
    prompt += "- Do not add explanations, code fences, timestamps, or extra blocks.\n"
    
    prompts_dir = BASE_DIR / "prompts"
    os.makedirs(prompts_dir, exist_ok=True)
    temp_prompt_path = prompts_dir / f"dynamic_prompt_{job_id}.md"
    
    with open(temp_prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
        
    return temp_prompt_path


def build_whisper_env(source_lang: Optional[str], timing_mode: Optional[str] = "auto") -> dict:
    env_vars = {"WHISPER_LANGUAGE": source_lang or "ko"}
    if timing_mode in {"word_cpu", "word_cpu_fast"}:
        env_vars.update(
            {
                "WHISPER_DEVICE": "cpu",
                "WHISPER_WORD_TIMESTAMPS": "true",
                "WHISPER_SNAP_START_TO_FIRST_WORD": "true",
            }
        )
    if timing_mode == "word_cpu_fast":
        env_vars.update(
            {
                "WHISPER_TEMPERATURES": "0,0.2,0.4,0.6",
                "WHISPER_NO_SPEECH_THRESHOLD": "0.8",
                "WHISPER_HALLUCINATION_SILENCE_THRESHOLD": "2.0",
                "WHISPER_CHUNK_SECONDS": "300",
                "WHISPER_CHUNK_OVERLAP_SECONDS": "1",
                "WHISPER_CPU_THREADS": "8",
            }
        )
    return env_vars


def cleanup_generated_files(job_id: str, paths: Optional[List[Path]]) -> None:
    if not paths:
        return
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                job_logs[job_id].append(f"[SYSTEM] Removed temporary file: {path.relative_to(BASE_DIR)}\n")
        except Exception as exc:
            job_logs[job_id].append(f"[WARNING] Failed to remove temporary file {path}: {exc}\n")


async def run_job_process(
    job_id: str,
    cmd: List[str],
    label: str,
    output_check_paths: List[Path] = None,
    env_vars: Optional[dict] = None,
    cleanup_paths: Optional[List[Path]] = None
):
    jobs[job_id]["status"] = "running"
    job_logs[job_id] = []
    
    # Check if executable in venv
    python_bin = str(BASE_DIR / ".venv" / "bin" / "python")
    if not os.path.exists(python_bin):
        python_bin = sys.executable  # Fallback to current python interpreter
        
    # Replace python command with virtualenv python if needed
    run_cmd = []
    for c in cmd:
        if c == "python3" or c == "python":
            run_cmd.append(python_bin)
        else:
            run_cmd.append(c)

    job_logs[job_id].append(f"[SYSTEM] Starting job: {label}\n")
    job_logs[job_id].append(f"[SYSTEM] Command: {' '.join(run_cmd)}\n\n")

    # Merge custom env vars with current os.environ
    run_env = os.environ.copy()
    if env_vars:
        for k, v in env_vars.items():
            run_env[k] = str(v)

    try:
        process = await asyncio.create_subprocess_exec(
            *run_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            preexec_fn=os.setsid,
            env=run_env
        )
        job_processes[job_id] = process
        
        # Stream output chunk by chunk to handle \r progress updates
        buffer = ""
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                if buffer:
                    job_logs[job_id].append(buffer)
                break
            
            buffer += chunk.decode('utf-8', errors='replace')
            while True:
                if '\n' in buffer and '\r' in buffer:
                    idx = min(buffer.find('\n'), buffer.find('\r'))
                elif '\n' in buffer:
                    idx = buffer.find('\n')
                elif '\r' in buffer:
                    idx = buffer.find('\r')
                else:
                    break
                    
                line = buffer[:idx+1]
                buffer = buffer[idx+1:]
                job_logs[job_id].append(line)
            
        await process.wait()
        
        if jobs[job_id]["status"] == "cancelling":
            jobs[job_id]["status"] = "cancelled"
            job_logs[job_id].append("\n[SYSTEM] Job was cancelled by user.\n")
        elif process.returncode == 0:
            jobs[job_id]["status"] = "completed"
            job_logs[job_id].append("\n[SYSTEM] Job completed successfully.\n")
            # Verify output files if paths provided
            if output_check_paths:
                found_any = False
                for p in output_check_paths:
                    if p.exists():
                        job_logs[job_id].append(f"[SYSTEM] Generated output file: {p.relative_to(BASE_DIR)}\n")
                        found_any = True
                if not found_any:
                    job_logs[job_id].append("[WARNING] Expected output files were not found.\n")
        else:
            jobs[job_id]["status"] = "failed"
            job_logs[job_id].append(f"\n[SYSTEM] Job failed with exit code {process.returncode}.\n")
            
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        job_logs[job_id].append(f"\n[SYSTEM] Exception during execution: {str(e)}\n")
    finally:
        cleanup_generated_files(job_id, cleanup_paths)
        if job_id in job_processes:
            del job_processes[job_id]

async def run_download_and_remux_job(
    job_id: str,
    url: str,
    quality: str,
    threads: int,
    start_offset: str,
    duration: Optional[str],
    temp_file: Path,
    output_file: Path,
    label: str
):
    jobs[job_id]["status"] = "running"
    job_logs[job_id] = []
    
    job_logs[job_id].append(f"[SYSTEM] Starting download job: {label}\n")
    
    try:
        # 1. Download
        dl_cmd, downloader = build_download_command(
            url, quality, threads, start_offset, duration, temp_file
        )
        job_logs[job_id].append(f"[SYSTEM] Step 1/2: Downloading video via {downloader}...\n")
        jobs[job_id]["step"] = "Downloading"
        success = await run_step(job_id, dl_cmd)
        if not success:
            jobs[job_id]["status"] = "failed"
            return
            
        # 2. Remux
        job_logs[job_id].append(f"\n[SYSTEM] Step 2/2: Remuxing video to valid MP4 container...\n")
        remux_cmd = [
            "ffmpeg", "-y", "-i", str(temp_file),
            "-c", "copy", "-avoid_negative_ts", "make_zero", str(output_file)
        ]
        jobs[job_id]["step"] = "Remuxing"
        success = await run_step(job_id, remux_cmd)
        
        if jobs[job_id]["status"] == "cancelling":
            jobs[job_id]["status"] = "cancelled"
            job_logs[job_id].append("\n[SYSTEM] Job was cancelled by user.\n")
        elif success:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["step"] = "Done"
            job_logs[job_id].append("\n[SYSTEM] Download and remux completed successfully!\n")
            if output_file.exists():
                job_logs[job_id].append(f"[SYSTEM] Generated output file: {output_file.relative_to(BASE_DIR)}\n")
        else:
            jobs[job_id]["status"] = "failed"
            job_logs[job_id].append("\n[SYSTEM] Remux step failed.\n")
            
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        job_logs[job_id].append(f"\n[SYSTEM] Exception during execution: {str(e)}\n")
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception as e:
                job_logs[job_id].append(f"[WARNING] Failed to delete temp file: {str(e)}\n")
        if job_id in job_processes:
            del job_processes[job_id]


async def run_full_pipeline(
    job_id: str,
    url: Optional[str],
    quality: str,
    threads: int,
    start_offset: str,
    duration: Optional[str],
    output_name: str,
    font_name: Optional[str],
    source_lang: str = "ko",
    video_path: Optional[str] = None,
    timing_mode: str = "auto",
    prompt_options: Optional[PromptOptions] = None
):
    jobs[job_id]["status"] = "running"
    job_logs[job_id] = []
    
    python_bin = str(BASE_DIR / ".venv" / "bin" / "python")
    if not os.path.exists(python_bin):
        python_bin = sys.executable

    # Filename prefix based on URL or timestamp if empty
    video_id = safe_download_name(output_name or "") or "video_" + str(uuid.uuid4())[:8]
    
    if url:
        # 1. Download Video & Lossless Remux
        download_dir = BASE_DIR / "video" / "download"
        video_file = download_dir / f"{video_id}.mp4"
        temp_video_file = download_dir / f"{video_id}.tmp.mp4"

    
    try:
        if url:
            job_logs[job_id].append(f"[SYSTEM] --- PIPELINE STEP 1/4: DOWNLOADING VIDEO ({video_id}.mp4) ---\n")
            dl_cmd, downloader = build_download_command(
                url, quality, threads, start_offset, duration, temp_video_file
            )
            job_logs[job_id].append(f"[SYSTEM] Downloader: {downloader}\n")
            
            jobs[job_id]["step"] = "Downloading"
            success = await run_step(job_id, dl_cmd)
            if not success:
                return
                
            job_logs[job_id].append(f"\n[SYSTEM] --- REMUXING VIDEO TO MP4 CONTAINER ---\n")
            remux_cmd = [
                "ffmpeg", "-y", "-i", str(temp_video_file),
                "-c", "copy", "-avoid_negative_ts", "make_zero", str(video_file)
            ]
            jobs[job_id]["step"] = "Remuxing"
            success = await run_step(job_id, remux_cmd)
            if not success:
                return
        else:
            # Use local video directly
            video_file = Path(video_path) if video_path else None
            if not video_file:
                job_logs[job_id].append(f"[ERROR] No URL or local video path provided.\n")
                jobs[job_id]["status"] = "failed"
                return
            if not video_file.is_absolute():
                video_file = BASE_DIR / video_file
            job_logs[job_id].append(f"[SYSTEM] --- PIPELINE STEP 1/4: USING LOCAL VIDEO ({video_file.name}) ---\n")
            video_id = video_file.stem
            
    finally:
        if url and temp_video_file.exists():
            try:
                temp_video_file.unlink()
            except Exception as e:
                job_logs[job_id].append(f"[WARNING] Failed to delete temp file: {str(e)}\n")
        
    # 2. Transcribe Video
    job_logs[job_id].append(f"\n[SYSTEM] --- PIPELINE STEP 2/4: TRANSCRIBING VIDEO ---\n")
    trans_cmd = [python_bin, "subtitle_pipeline.py", "transcribe", str(video_file)]
    
    # Do NOT append --start-time and --duration here if we downloaded via streamlink,
    # because the video_file is already cut to the exact length.
    # Only append if local video AND user somehow specified offset/duration (not currently in UI)
    if not url:
        if start_offset != "00:00:00":
            trans_cmd.extend(["--start-time", start_offset])
        if duration and duration != "01:00:00":
            parts = duration.split(":")
            if len(parts) == 3:
                sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                trans_cmd.extend(["--duration", str(sec)])
            
    jobs[job_id]["step"] = "Transcribing"
    success = await run_step(job_id, trans_cmd, env_vars=build_whisper_env(source_lang, timing_mode))
    if not success:
        return

    # Check transcribed path
    raw_srt_path = BASE_DIR / "transcribe" / f"{video_id}.raw.srt"
    if not raw_srt_path.exists():
        # Maybe it transcribed directly under another derived name? Check the dir
        # stt-video handles naming inside. Let's try to locate the newest .raw.srt
        srts = sorted(Path(BASE_DIR / "transcribe").glob(f"*{video_id}*.raw.srt"), key=os.path.getmtime)
        if srts:
            raw_srt_path = srts[-1]
        else:
            job_logs[job_id].append(f"[ERROR] Transcribe output raw.srt not found for ID: {video_id}\n")
            jobs[job_id]["status"] = "failed"
            return

    # 3. Translate SRT
    job_logs[job_id].append(f"\n[SYSTEM] --- PIPELINE STEP 3/4: TRANSLATING SUBTITLES ---\n")
    generated_prompt_path = None
    
    # Map source language to correct prompt path
    if prompt_options:
        prompt_path = generate_dynamic_prompt(source_lang, prompt_options, job_id)
        generated_prompt_path = prompt_path
        job_logs[job_id].append(f"[SYSTEM] Using dynamically generated translation prompt.\n")
    else:
        prompt_file = "korean-thai-livestream.md"
        if source_lang == "zh":
            prompt_file = "chinese-thai-livestream.md"
        elif source_lang == "ja":
            prompt_file = "japanese-thai-livestream.md"
            
        prompt_path = BASE_DIR / "prompts" / prompt_file
    
    translate_cmd = [python_bin, "subtitle_pipeline.py", "translate", str(raw_srt_path), "--translation-prompt", str(prompt_path)]
    
    jobs[job_id]["step"] = "Translating"
    try:
        success = await run_step(job_id, translate_cmd)
    finally:
        cleanup_generated_files(job_id, [generated_prompt_path] if generated_prompt_path else None)
    if not success:
        return

    # Check translated path
    translated_srt_path = BASE_DIR / "translate" / raw_srt_path.name.replace(".raw.srt", ".raw.translated.srt")
    if not translated_srt_path.exists():
        translated_srts = sorted(Path(BASE_DIR / "translate").glob(f"*{video_id}*translated.srt"), key=os.path.getmtime)
        if translated_srts:
            translated_srt_path = translated_srts[-1]
        else:
            job_logs[job_id].append(f"[ERROR] Translated srt not found for ID: {video_id}\n")
            jobs[job_id]["status"] = "failed"
            return

    # 4. Burn/Mux Subtitle (Defaults to Mux MKV since it's fast, but can use burn if font_name is selected)
    job_logs[job_id].append(f"\n[SYSTEM] --- PIPELINE STEP 4/4: INTEGRATING SUBTITLES ---\n")
    
    if font_name:
        # Burn Hard Sub
        burn_cmd = [python_bin, "subtitle_pipeline.py", "burn", str(video_file), str(translated_srt_path)]
        if font_name != "Default":
            burn_cmd.extend(["--font-name", font_name])
        jobs[job_id]["step"] = "Burning"
        success = await run_step(job_id, burn_cmd)
    else:
        # Mux Soft Sub
        mkv_file = BASE_DIR / "video" / f"{video_id}.mkv"
        mux_cmd = [
            "ffmpeg", "-y", "-i", str(video_file), "-i", str(translated_srt_path),
            "-map", "0:v?", "-map", "0:a?", "-map", "1", "-c", "copy", str(mkv_file)
        ]
        jobs[job_id]["step"] = "Muxing"
        success = await run_step(job_id, mux_cmd)
        
    if success:
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["step"] = "Done"
        job_logs[job_id].append("\n[SYSTEM] PIPELINE COMPLETED SUCCESSFULLY!\n")
    else:
        jobs[job_id]["status"] = "failed"

async def run_step(job_id: str, cmd: List[str], env_vars: Optional[dict] = None) -> bool:
    job_logs[job_id].append(f"[SYSTEM] Command: {redacted_command(cmd)}\n")
    
    run_env = os.environ.copy()
    if env_vars:
        for k, v in env_vars.items():
            run_env[k] = str(v)
            
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            preexec_fn=os.setsid,
            env=run_env
        )
        job_processes[job_id] = process
        
        buffer = ""
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                if buffer:
                    job_logs[job_id].append(buffer)
                break
                
            buffer += chunk.decode('utf-8', errors='replace')
            while True:
                if '\n' in buffer and '\r' in buffer:
                    idx = min(buffer.find('\n'), buffer.find('\r'))
                elif '\n' in buffer:
                    idx = buffer.find('\n')
                elif '\r' in buffer:
                    idx = buffer.find('\r')
                else:
                    break
                    
                line = buffer[:idx+1]
                buffer = buffer[idx+1:]
                job_logs[job_id].append(line)
            
        await process.wait()
        
        if jobs[job_id]["status"] == "cancelling":
            job_logs[job_id].append("\n[SYSTEM] Job was cancelled by user.\n")
            return False
            
        if process.returncode == 0:
            job_logs[job_id].append("[SYSTEM] Step completed successfully.\n")
            return True
        else:
            job_logs[job_id].append(f"[SYSTEM] Step failed with exit code {process.returncode}.\n")
            return False
    except Exception as e:
        job_logs[job_id].append(f"[SYSTEM] Error in step: {str(e)}\n")
        return False
    finally:
        if job_id in job_processes:
            del job_processes[job_id]


@app.get("/api/status")
def get_status():
    ffmpeg_ok = os.system("which ffmpeg > /dev/null") == 0
    streamlink_ok = os.system("which streamlink > /dev/null") == 0
    
    return {
        "ffmpeg": ffmpeg_ok,
        "streamlink": streamlink_ok,
        "environment": status_environment(),
        "chzzk_auth_configured": bool(
            os.getenv("CHZZK_NID_AUT") and os.getenv("CHZZK_NID_SES")
        ),
        "system_python": sys.executable,
        "venv_exists": os.path.exists(BASE_DIR / ".venv")
    }

@app.get("/api/files")
def get_files():
    def list_files_in_dir(
        directory: Path,
        allowed_extensions: List[str],
        *,
        kind: str,
        include: Optional[Callable[[Path], bool]] = None
    ) -> List[dict]:
        if not directory.exists():
            return []
        files = []
        for file_path in directory.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in allowed_extensions:
                # Exclude hidden files or cache directories
                if "/." in file_path.as_posix() or "\\." in file_path.as_posix():
                    continue
                if include and not include(file_path):
                    continue
                relative_path = file_path.relative_to(BASE_DIR)
                files.append({
                    "name": file_path.name,
                    "rel_path": str(relative_path),
                    "abs_path": str(file_path),
                    "directory": str(relative_path.parent),
                    "kind": kind,
                    "size_bytes": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime
                })
        # Sort by modified time (newest first)
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files

    video_downloads = list_files_in_dir(
        BASE_DIR / "video" / "download",
        [".mp4", ".mkv"],
        kind="downloaded_video",
    )
    videos = list_files_in_dir(BASE_DIR / "video", [".mp4", ".mkv"], kind="video")
    # Filter downloads out of main videos list to avoid duplicates
    videos = [v for v in videos if "download/" not in v["rel_path"]]
    output_videos = [v for v in videos if v["rel_path"].endswith((".mkv", ".subtitled.mp4"))]

    transcripts = list_files_in_dir(
        BASE_DIR / "transcribe",
        [".srt"],
        kind="raw_transcript",
        include=lambda path: path.name.endswith(".raw.srt"),
    )
    whisper_transcripts = list_files_in_dir(
        BASE_DIR / "transcribe",
        [".srt"],
        kind="whisper_transcript",
        include=lambda path: path.name.endswith(".raw_whisper.srt"),
    )
    translations = list_files_in_dir(
        BASE_DIR / "translate",
        [".srt"],
        kind="translated_subtitle",
        include=lambda path: path.name.endswith(".translated.srt"),
    )

    return {
        "video_downloads": video_downloads,
        "videos": videos,
        "output_videos": output_videos,
        "transcripts": transcripts,
        "whisper_transcripts": whisper_transcripts,
        "translations": translations
    }

@app.get("/api/download-file")
async def download_file(path: str):
    file_path = BASE_DIR / path
    # Secure check path to prevent path traversal
    try:
        file_path.relative_to(BASE_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
        
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        path=file_path, 
        filename=file_path.name,
        media_type="application/octet-stream"
    )

@app.get("/api/jobs")
async def get_jobs():
    return {jid: {
        "status": jobs[jid]["status"],
        "label": jobs[jid]["label"],
        "step": jobs[jid].get("step", "N/A"),
        "created_at": jobs[jid]["created_at"]
    } for jid in jobs}

@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    # Resolve output name
    if req.output_name:
        video_id = safe_download_name(req.output_name)
    else:
        # Extract video ID from Naver URL
        # e.g., https://chzzk.naver.com/video/13469305
        video_id = req.url.split("/")[-1].split("?")[0]
        if not video_id.isdigit():
            video_id = "download_" + str(uuid.uuid4())[:8]
            
    output_file = BASE_DIR / "video" / "download" / f"{video_id}.mp4"
    temp_file = BASE_DIR / "video" / "download" / f"{video_id}.tmp.mp4"
    
    jobs[job_id] = {
        "status": "pending",
        "label": f"Download Naver Video {video_id}",
        "step": "Starting",
        "created_at": asyncio.get_event_loop().time()
    }
    
    background_tasks.add_task(
        run_download_and_remux_job,
        job_id,
        req.url,
        req.quality,
        req.threads,
        req.start_offset,
        req.duration,
        temp_file,
        output_file,
        jobs[job_id]["label"]
    )
    
    return {"job_id": job_id, "label": jobs[job_id]["label"]}

@app.post("/api/transcribe")
async def start_transcribe(req: TranscribeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    video_file = Path(req.video_path)
    
    # Validate paths
    if not video_file.is_absolute():
        video_file = BASE_DIR / video_file
        
    if not video_file.exists():
        raise HTTPException(status_code=400, detail="Video file not found")
        
    cmd = ["python3", "subtitle_pipeline.py", "transcribe", str(video_file)]
    
    if req.start_time:
        cmd.extend(["--start-time", req.start_time])
    if req.duration:
        cmd.extend(["--duration", req.duration])
        
    jobs[job_id] = {
        "status": "pending",
        "label": f"Transcribe {video_file.name}",
        "step": "Starting",
        "created_at": asyncio.get_event_loop().time()
    }
    
    # Expected output files
    expected_srt = BASE_DIR / "transcribe" / video_file.name.replace(video_file.suffix, ".raw.srt")
    
    background_tasks.add_task(
        run_job_process, 
        job_id, 
        cmd, 
        jobs[job_id]["label"], 
        [expected_srt],
        build_whisper_env(req.source_lang, req.timing_mode)
    )
    
    return {"job_id": job_id, "label": jobs[job_id]["label"]}

@app.post("/api/translate")
async def start_translate(req: TranslateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    srt_file = Path(req.input_srt)
    
    if not srt_file.is_absolute():
        srt_file = BASE_DIR / srt_file
        
    if not srt_file.exists():
        raise HTTPException(status_code=400, detail="SRT file not found")
        
    # Map source_lang to prompt if req.prompt_path is not specified
    prompt_path = req.prompt_path
    cleanup_paths = None
    if not prompt_path:
        if req.prompt_options:
            generated_prompt_path = generate_dynamic_prompt(req.source_lang, req.prompt_options, job_id)
            prompt_path = str(generated_prompt_path)
            cleanup_paths = [generated_prompt_path]
        else:
            prompt_file = "korean-thai-livestream.md"
            if req.source_lang == "zh":
                prompt_file = "chinese-thai-livestream.md"
            elif req.source_lang == "ja":
                prompt_file = "japanese-thai-livestream.md"
            prompt_path = str(BASE_DIR / "prompts" / prompt_file)
        
    cmd = ["python3", "subtitle_pipeline.py", "translate", str(srt_file), "--translation-prompt", prompt_path]
    
    if req.model:
        cmd.extend(["--model", req.model])
        
    jobs[job_id] = {
        "status": "pending",
        "label": f"Translate {srt_file.name}",
        "step": "Starting",
        "created_at": asyncio.get_event_loop().time()
    }
    
    expected_translated = BASE_DIR / "translate" / srt_file.name.replace(".raw.srt", ".raw.translated.srt")
    
    background_tasks.add_task(
        run_job_process, 
        job_id, 
        cmd, 
        jobs[job_id]["label"], 
        [expected_translated],
        cleanup_paths=cleanup_paths
    )
    
    return {"job_id": job_id, "label": jobs[job_id]["label"]}

@app.post("/api/integrate")
async def start_integrate(req: IntegrateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    video_file = Path(req.video_path)
    srt_file = Path(req.srt_path)
    
    if not video_file.is_absolute():
        video_file = BASE_DIR / video_file
    if not srt_file.is_absolute():
        srt_file = BASE_DIR / srt_file
        
    if not video_file.exists() or not srt_file.exists():
        raise HTTPException(status_code=400, detail="Video or SRT file not found")
        
    if req.mode == "mux":
        # Mux soft sub to mkv
        out_name = video_file.stem + ".mkv"
        out_file = BASE_DIR / "video" / out_name
        # ffmpeg -y -i video.mp4 -i sub.srt -map 0:v? -map 0:a? -map 1 -c copy out.mkv
        cmd = [
            "ffmpeg", "-y", "-i", str(video_file), "-i", str(srt_file),
            "-map", "0:v?", "-map", "0:a?", "-map", "1", "-c", "copy", str(out_file)
        ]
        label = f"Mux Subtitles to MKV: {video_file.name}"
    else:
        # Burn hard sub to mp4
        out_file = BASE_DIR / "video" / "burn" / (video_file.stem + ".subtitled.mp4")
        cmd = [
            "python3",
            "subtitle_pipeline.py",
            "burn",
            str(video_file),
            str(srt_file),
            "--output-dir",
            str(BASE_DIR / "video"),
        ]
        if req.font_name:
            cmd.extend(["--font-name", req.font_name])
        label = f"Burn Subtitles to MP4: {video_file.name}"
        
    jobs[job_id] = {
        "status": "pending",
        "label": label,
        "step": "Starting",
        "created_at": asyncio.get_event_loop().time()
    }
    
    background_tasks.add_task(
        run_job_process, 
        job_id, 
        cmd, 
        jobs[job_id]["label"], 
        [out_file]
    )
    
    return {"job_id": job_id, "label": jobs[job_id]["label"]}

@app.post("/api/pipeline")
async def start_full_pipeline(req: FullPipelineRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    jobs[job_id] = {
        "status": "pending",
        "label": f"Full Pipeline for URL: {req.url}",
        "step": "Starting Download",
        "created_at": asyncio.get_event_loop().time()
    }
    
    background_tasks.add_task(
        run_full_pipeline,
        job_id,
        req.url,
        req.quality,
        req.threads,
        req.start_offset,
        req.duration,
        req.output_name,
        req.font_name,
        req.source_lang,
        req.video_path,
        req.timing_mode,
        req.prompt_options
    )
    
    return {"job_id": job_id, "label": jobs[job_id]["label"]}

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    if jobs[job_id]["status"] not in ["running", "pending"]:
        return {"status": "ignored", "message": f"Job status is {jobs[job_id]['status']}"}
        
    jobs[job_id]["status"] = "cancelling"
    
    if job_id in job_processes:
        process = job_processes[job_id]
        try:
            # Send SIGKILL to the entire process group (process and its children)
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            job_logs[job_id].append("\n[SYSTEM] Sent SIGKILL to process group (Force Stop)...\n")
        except Exception as e:
            # Fallback to direct process kill if PGID fails
            try:
                process.kill()
                job_logs[job_id].append(f"\n[SYSTEM] Sent direct SIGKILL to process: {str(e)}\n")
            except Exception as inner_e:
                job_logs[job_id].append(f"\n[SYSTEM] Failed to terminate process: {str(inner_e)}\n")
            
    return {"status": "cancelled", "job_id": job_id}

@app.get("/api/jobs/{job_id}/logs")
async def get_job_logs_stream(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    async def log_generator():
        last_idx = 0
        while True:
            # Yield new lines
            current_len = len(job_logs.get(job_id, []))
            if last_idx < current_len:
                for i in range(last_idx, current_len):
                    line = job_logs[job_id][i]
                    # Escape newlines and carriage returns for SSE format
                    escaped_line = line.replace('\n', '\\n').replace('\r', '\\r')
                    yield f"data: {escaped_line}\n\n"
                last_idx = current_len
                
            # If job finished, signal end
            status = jobs.get(job_id, {}).get("status")
            if status in ["completed", "failed", "cancelled"] and last_idx >= len(job_logs.get(job_id, [])):
                yield "data: [SYSTEM] EOF\n\n"
                break
                
            await asyncio.sleep(0.3)
            
    return StreamingResponse(log_generator(), media_type="text/event-stream")


# Serve static files last
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
