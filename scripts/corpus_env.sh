#!/usr/bin/env bash
# Export every corpus variable the suite knows, from one root.
#
#     source scripts/corpus_env.sh            # E:/ACIDcat_hunting, or $ACIDCAT_HUNT_CORPUS
#     source scripts/corpus_env.sh /mnt/hunt  # another root
#
# The corpus tests skip without these, and a skipped corpus test is a green
# run that checked no real file. Run them on the release commit at least.
root="${1:-${ACIDCAT_HUNT_CORPUS:-E:/ACIDcat_hunting}}"
export ACIDCAT_HUNT_CORPUS="$root"
export ACIDCAT_SPC_CORPUS="$root/Nintendo SPC"
export ACIDCAT_PSF_CORPUS="$root/Gameboy Sound Format"
export ACIDCAT_GBS_CORPUS="$root/Gameboy Sound System"
export ACIDCAT_HES_CORPUS="$root/HES"
export ACIDCAT_KSS_CORPUS="$root/KSS"
export ACIDCAT_VGM_CORPUS="$root/Video Game Music"
export ACIDCAT_PT3_CORPUS="$root/Spectrum"
export ACIDCAT_STC_CORPUS="$root/Spectrum"
export ACIDCAT_SOUNDTRACKER_CORPUS="$root/Soundtracker"
export ACIDCAT_MDX_CORPUS="$root/MDX_files/extracted_mdx"
export ACIDCAT_PMD_CORPUS="$root/PMD"
export ACIDCAT_S3P_CORPUS="$root/nx68000"
export ACIDCAT_DSD_CORPUS="$root/Audiophile Formats"
export ACIDCAT_SID_CORPUS="$root/SID_files/C64Music"
export ACIDCAT_SAP_CORPUS="$root/Atari_SAP"
export ACIDCAT_NSF_CORPUS="$root/NSFe"
export ACIDCAT_KRZ_CORPUS="$root/Kurzweil"
export ACIDCAT_ABLETON_CORPUS="$root/Ancient Ableton"
# the OKI decoder's oracle; found on PATH if not set
if [ -z "${ACIDCAT_FFMPEG:-}" ] && ! command -v ffmpeg >/dev/null 2>&1; then
    f=$(ls "$LOCALAPPDATA"/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe 2>/dev/null | head -1)
    [ -n "$f" ] && export ACIDCAT_FFMPEG="$f"
fi
