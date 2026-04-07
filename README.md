# stt-video

สคริปต์สำหรับทำซับวิดีโอเกาหลีเป็นภาษาไทยแบบครบ pipeline:

1. ดึงเสียงจากวิดีโอด้วย `ffmpeg`
2. ถอดเสียงเกาหลีด้วย Whisper
3. แปลไฟล์ `.srt` เป็นภาษาไทยผ่าน OpenAI-compatible API
4. ฝังซับลงในวิดีโอด้วย `ffmpeg`

เหมาะกับงานลักษณะ VOD / livestream ที่ต้องการซับไทยอ่านง่ายและพร้อมใช้งานต่อทันที

## ความสามารถหลัก

- รองรับรันทีละขั้น หรือรันทั้ง pipeline ในคำสั่งเดียว
- ใช้ Whisper `large-v3` สำหรับถอดเสียงภาษาเกาหลี
- แปลซับผ่าน API ที่ compatible กับ OpenAI เช่น OpenAI-compatible gateway หรือ LM Studio
- ทำความสะอาด transcript เบื้องต้น เช่น ลบ filler และลดบรรทัดซ้ำยาวผิดปกติ
- รองรับตัดเฉพาะช่วงของวิดีโอด้วย `--start-time` และ `--duration`
- เลือกฟอนต์ซับได้ผ่าน `--font-name` หรือ `SUBTITLE_FONT`

## Requirements

- Python 3.10+
- `ffmpeg`
- แนะนำให้มี `fc-list` จาก `fontconfig` ถ้าต้องการให้สคริปต์ตรวจหาฟอนต์ไทยอัตโนมัติ
- ฟอนต์ภาษาไทยในเครื่อง เช่น `Sarabun`, `TH Sarabun New`, `Noto Sans Thai`

หมายเหตุ:

- ครั้งแรกที่รัน Whisper อาจมีการโหลดโมเดล `large-v3`
- ถ้าจะใช้ local endpoint เช่น `http://localhost:1234` สคริปต์จะเติม `/v1` ให้อัตโนมัติ
- ถ้า base URL เป็น `localhost` หรือ `127.0.0.1` สามารถปล่อย `LLM_API_KEY` ว่างได้ สคริปต์จะใช้ค่า placeholder ให้เอง

## Installation

ติดตั้ง Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

หรือใช้ `make`:

```bash
make install
```

## Configuration

สร้างไฟล์ `.env` จาก `.env.sample`:

```bash
cp .env.sample .env
```

ตั้งค่าอย่างน้อย:

```env
LLM_BASE_URL="http://localhost:1234"
LLM_MODEL="gemini-2.0-flash"
LLM_API_KEY=""
```

ตัวแปรเสริมที่สคริปต์รองรับ:

- `SUBTITLE_FONT` เลือกฟอนต์สำหรับ burn subtitle
- `WHISPER_DEVICE` บังคับ device เป็น `cpu`, `mps`, หรือ `cuda`
- `WHISPER_CHUNK_SECONDS` ความยาวต่อ chunk ตอนถอดเสียง ค่า default คือ `900`
- `WHISPER_CHUNK_OVERLAP_SECONDS` overlap ระหว่าง chunk ค่า default คือ `1.5`
- `LLM_TRANSLATE_BLOCKS_PER_CHUNK` จำนวน block ต่อรอบตอนแปล ค่า default คือ `60`
- `LLM_TRANSLATE_CONTEXT_BLOCKS` จำนวน block ก่อนหน้าที่ส่งเป็น context ตอนแปล ค่า default คือ `4`

## Quick Start

รันครบทั้ง pipeline:

```bash
.venv/bin/python subtitle_pipeline.py all /path/to/video.mp4 --output-dir ./out
```

หรือถ้าไม่ใส่ subcommand สคริปต์จะถือว่าเป็น `all` ให้อัตโนมัติ:

```bash
.venv/bin/python subtitle_pipeline.py /path/to/video.mp4 --output-dir ./out
```

ใช้ `make`:

```bash
make all VIDEO=/path/to/video.mp4 OUT=./out \
  LLM_BASE_URL=http://localhost:1234 \
  LLM_MODEL=gemini-2.0-flash \
  LLM_API_KEY=
```

## Usage

### 1) Transcribe

```bash
.venv/bin/python subtitle_pipeline.py transcribe /path/to/video.mp4 --output-dir ./out
```

รองรับตัดเฉพาะช่วง:

```bash
.venv/bin/python subtitle_pipeline.py transcribe /path/to/video.mp4 \
  --start-time 00:10:00 \
  --duration 300 \
  --output-dir ./out
```

หรือ:

```bash
make transcribe VIDEO=/path/to/video.mp4 OUT=./out START=00:10:00 DURATION=300
```

### 2) Translate

```bash
.venv/bin/python subtitle_pipeline.py translate /path/to/raw.srt --output-dir ./out
```

ระบุ LLM settings ผ่าน flag ได้:

```bash
.venv/bin/python subtitle_pipeline.py translate /path/to/raw.srt \
  --llm-base-url http://localhost:1234 \
  --llm-model gemini-2.0-flash \
  --llm-api-key "" \
  --output-dir ./out
```

หรือ:

```bash
make translate SRT=/path/to/raw.srt OUT=./out \
  LLM_BASE_URL=http://localhost:1234 \
  LLM_MODEL=gemini-2.0-flash \
  LLM_API_KEY=
```

### 3) Burn Subtitle

```bash
.venv/bin/python subtitle_pipeline.py burn /path/to/video.mp4 /path/to/translated.srt \
  --font-name "Sarabun" \
  --output-dir ./out
```

หรือ:

```bash
make burn VIDEO=/path/to/video.mp4 SRT=/path/to/translated.srt OUT=./out FONT=Sarabun
```

### 4) All-in-one

```bash
.venv/bin/python subtitle_pipeline.py all /path/to/video.mp4 \
  --start-time 00:10:00 \
  --duration 300 \
  --font-name "Sarabun" \
  --output-dir ./out
```

## Output Structure

เมื่อใช้ `all --output-dir ./out` จะได้ประมาณนี้:

```text
out/
├── transcribe/
│   ├── video.audio.wav
│   ├── video.raw_whisper.srt
│   └── video.raw.srt
├── translate/
│   └── video.raw.translated.srt
└── burn/
    └── video.subtitled.mp4
```

รายละเอียด:

- `raw_whisper.srt` คือ output ตรงจาก Whisper
- `raw.srt` คือ transcript หลังผ่าน cleanup
- `translated.srt` คือซับไทยที่แปลแล้ว
- `subtitled.mp4` คือวิดีโอที่ฝังซับเรียบร้อย

## Notes

- ขั้นตอน burn subtitle ต้องใช้ฟอนต์ที่ `ffmpeg` มองเห็นได้จริง ไม่เช่นนั้นอาจได้ฟอนต์ fallback ที่ไม่สวยหรือภาษาไทยแสดงผลผิด
- ถ้าเครื่องไม่มี `fc-list` สคริปต์จะยังรันได้ แต่จะ fallback ไปใช้ชื่อฟอนต์ที่กำหนดไว้ตรงๆ
- ถ้าไฟล์ SRT ว่าง หรือ LLM ตอบกลับรูปแบบไม่ตรง สคริปต์จะหยุดพร้อม error message

## Useful Commands

ดู help:

```bash
python3 subtitle_pipeline.py --help
python3 subtitle_pipeline.py transcribe --help
python3 subtitle_pipeline.py translate --help
python3 subtitle_pipeline.py burn --help
python3 subtitle_pipeline.py all --help
```
