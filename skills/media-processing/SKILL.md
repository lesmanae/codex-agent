# Media Processing (Video / Audio / Image / Subtitle) — Skill

**Trigger phrases**: ffmpeg, video convert, audio convert, encode video, decode
video, transcode, mp4, mkv, webm, avi, mov, mp3, m4a, aac, flac, ogg, opus,
wav, png, jpg, jpeg, webp, gif, svg, image resize, image compress, watermark,
crop, trim, cut video, merge video, concat video, extract audio, extract
frames, screenshot video, thumbnail, subtitle, srt, ass, vtt, burn subtitle,
hardsub, softsub, overlay, scale, pad, rotate, flip, deinterlace, framerate,
fps, bitrate, codec, h264, h265, hevc, av1, vp9, hls, dash, livestream, rtmp,
youtube-dl, yt-dlp, download video, normalize audio, denoise, ebur128, peak,
lufs, imagemagick, convert command, magick, exiftool, exif metadata.

Use when the user asks to convert, edit, compress, or extract from video,
audio, image, or subtitle files. Audience: someone who wants the right
ffmpeg/imagemagick command, fast.

---

## Operating principles

- **Stream-copy when format is the only change.** `-c copy` skips
  re-encoding — instant + lossless.
- **Always set `-y` for batch overwrites; never globally.** Confirm the
  output path doesn't exist for one-off ops.
- **Re-encode? Use sane defaults.** H.264 CRF 23, AAC 192k, libwebp q 80,
  jpeg q 85.
- **Use `-progress pipe:1`** in scripts to parse percentage cleanly.
- **Read input metadata first** (`ffprobe`/`exiftool`) to avoid wrong
  assumptions about resolution/fps/codec.

---

## Toolbox

```bash
which ffmpeg ffprobe yt-dlp magick convert exiftool sox
ffmpeg -version | head -1
```

If missing: `apt-get install -y ffmpeg imagemagick exiftool yt-dlp sox`.

---

## ffprobe (inspect first)

```bash
ffprobe -v error -hide_banner -show_format -show_streams -of json input.mp4 | jq
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration -of csv=p=0 input.mp4
```

## Cut/trim without re-encode

```bash
ffmpeg -ss 00:00:30 -i in.mp4 -to 00:01:00 -c copy out.mp4
```

`-ss` before `-i` is fast (seek before decoding). For frame-accurate cut,
move `-ss` after `-i` and re-encode.

## Concat (same codec)

```bash
# concat_list.txt:
#   file 'a.mp4'
#   file 'b.mp4'
ffmpeg -f concat -safe 0 -i concat_list.txt -c copy out.mp4
```

## Concat (different codecs) → re-encode

```bash
ffmpeg -i a.mp4 -i b.mp4 -filter_complex \
  "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]" -map "[v]" -map "[a]" out.mp4
```

## Extract audio

```bash
ffmpeg -i in.mp4 -vn -c:a copy out.m4a            # no re-encode (if AAC)
ffmpeg -i in.mp4 -vn -c:a libmp3lame -q:a 2 out.mp3   # to mp3
```

## Extract a single frame / thumbnail

```bash
ffmpeg -ss 00:00:05 -i in.mp4 -frames:v 1 -q:v 2 thumb.jpg
ffmpeg -i in.mp4 -vf "thumbnail" -frames:v 1 smart-thumb.jpg
```

## Convert / re-encode video

```bash
# H.264 + AAC, web-friendly
ffmpeg -i in.mkv -c:v libx264 -preset medium -crf 23 \
       -c:a aac -b:a 192k -movflags +faststart out.mp4

# H.265 (smaller, slower)
ffmpeg -i in.mp4 -c:v libx265 -preset medium -crf 28 -tag:v hvc1 \
       -c:a aac -b:a 192k out.mp4

# WebM / VP9
ffmpeg -i in.mp4 -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus -b:a 128k out.webm
```

## Resize / scale

```bash
ffmpeg -i in.mp4 -vf "scale=-2:720" out.mp4         # height 720, width auto-even
ffmpeg -i in.mp4 -vf "scale=1280:720,setsar=1" out.mp4
```

## Burn subtitles into video

```bash
ffmpeg -i in.mp4 -vf "subtitles=subs.srt:force_style='FontSize=22,Outline=1'" \
       -c:v libx264 -crf 23 -c:a copy out.mp4
```

## Soft subtitle (mux without burning)

```bash
ffmpeg -i in.mp4 -i subs.srt -c copy -c:s mov_text out.mp4
```

## Normalize loudness (broadcast-spec)

```bash
ffmpeg -i in.wav -af loudnorm=I=-16:TP=-1.5:LRA=11 -ar 48000 out.wav
```

## Mute or replace audio

```bash
ffmpeg -i in.mp4 -an out.mp4
ffmpeg -i in.mp4 -i music.m4a -map 0:v -map 1:a -c:v copy -shortest out.mp4
```

## GIF from video

```bash
ffmpeg -i in.mp4 -vf "fps=15,scale=480:-1:flags=lanczos,palettegen" /tmp/p.png
ffmpeg -i in.mp4 -i /tmp/p.png -lavfi "fps=15,scale=480:-1[x];[x][1:v]paletteuse" out.gif
```

---

## ImageMagick (single-image ops)

Use the modern `magick` command (v7+); fall back to `convert` (v6).

```bash
magick in.jpg -resize 1024x out.jpg
magick in.png -strip -quality 85 out.jpg              # also strip metadata
magick in.png -auto-orient -resize 800x800^ -gravity center -extent 800x800 out.jpg
magick in.jpg -fill black -font DejaVu-Sans -pointsize 36 -gravity South \
       -annotate +0+20 'WATERMARK' out.jpg

# convert format
magick in.heic out.jpg
magick in.svg -density 300 -background none out.png

# strip EXIF
exiftool -all= in.jpg
```

Bulk:
```bash
for f in *.png; do magick "$f" -resize 50% "thumbs/$f"; done
```

---

## yt-dlp (download from URL)

```bash
yt-dlp -f 'bv*+ba/b' "https://youtu.be/abc"          # best video + audio
yt-dlp -f 'bestaudio' -x --audio-format mp3 URL       # audio only as mp3
yt-dlp --write-subs --sub-langs en,id URL
yt-dlp -F URL                                          # list formats
```

Respect: only your own content or content with explicit redistribution permission.

---

## Pitfalls

- `-c copy` after a filter doesn't work — copy means "don't re-encode",
  but filters require a decode. Drop `-c copy`.
- Many streaming sources use HLS — pass the `.m3u8` URL to ffmpeg
  directly with `-protocol_whitelist file,http,https,tcp,tls,crypto`.
- Output looks corrupt in some players → add `-movflags +faststart` for
  MP4 web playback.
- ImageMagick by default has tiny resource limits in
  `/etc/ImageMagick-*/policy.xml`; large images need limits raised.
