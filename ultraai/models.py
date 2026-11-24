"""Every model the pipeline can use, with display names and measured benchmark results.

Light module (no heavy imports): used by the Studio's model picker and by the worker.
Benchmark numbers come from ``model_bench.json`` (scripts/bench_export.py, made from
scripts/bench_models.py runs against hand-made songs).
"""

from __future__ import annotations

import json
from pathlib import Path

BENCH_FILE = Path(__file__).with_name("model_bench.json")

# key -> (name, by)   keys are what the worker gets (registry key or the model file name)
SEPARATION = {
    "htdemucs": ("HTDemucs", "Meta"),
    "htdemucs_ft": ("HTDemucs fine-tuned", "Meta"),
    "hdemucs_mmi.yaml": ("HDemucs MMI", "Meta"),
    "UVR-MDX-NET-Voc_FT.onnx": ("MDX-Net Voc FT", "UVR"),
    "Kim_Vocal_2.onnx": ("MDX-Net Kim Vocal 2", "Kimberley Jensen"),
    "UVR-MDX-NET_Main_406.onnx": ("MDX-Net Main 406", "UVR"),
    "MDX23C-8KFFT-InstVoc_HQ.ckpt": ("MDX23C InstVoc HQ", "ZFTurbo"),
    "melband": ("MelBand RoFormer", "Kimberley Jensen"),
    "mel_band_roformer_kim_ft_unwa.ckpt": ("MelBand RoFormer Kim FT", "unwa"),
    "melband_roformer_big_beta4.ckpt": ("MelBand RoFormer Big Beta 4", "unwa"),
    "melband_roformer_big_beta5e.ckpt": ("MelBand RoFormer Big Beta 5e", "unwa"),
    "MelBandRoformerBigSYHFTV1.ckpt": ("MelBand RoFormer Big SYHFT V1", "SYH99999"),
    "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt": ("MelBand RoFormer", "viperx"),
    "melband_roformer_instvox_duality_v2.ckpt": ("MelBand RoFormer InstVoc Duality v2", "unwa"),
    "model_bs_roformer_ep_368_sdr_12.9628.ckpt": ("BS-RoFormer 1296", "viperx"),
    "model_bs_roformer_ep_317_sdr_12.9755.ckpt": ("BS-RoFormer 1297", "viperx"),
}
PITCH = {
    "swift": ("SwiftF0", ""), "rmvpe": ("RMVPE", "RVC project"), "fcpe": ("FCPE", ""),
    "pesto": ("PESTO", "Sony CSL"), "crepe-tiny": ("CREPE tiny", ""), "crepe-full": ("CREPE full", ""),
    "vote": ("Vote: SwiftF0 + RMVPE + FCPE", ""),
}
LEAD = {
    "": ("Keep backing vocals", ""),
    "kara2": ("MDX-Net Karaoke 2", "UVR"),
    "UVR_MDXNET_KARA.onnx": ("MDX-Net Karaoke 1", "UVR"),
    "5_HP-Karaoke-UVR.pth": ("VR HP Karaoke 5", "UVR"),
    "6_HP-Karaoke-UVR.pth": ("VR HP Karaoke 6", "UVR"),
    "melkara": ("MelBand RoFormer Karaoke", "aufr33 & viperx"),
    "mel_band_roformer_karaoke_gabox.ckpt": ("MelBand RoFormer Karaoke", "gabox"),
    "mel_band_roformer_karaoke_gabox_v2.ckpt": ("MelBand RoFormer Karaoke v2", "gabox"),
    "mel_band_roformer_karaoke_becruily.ckpt": ("MelBand RoFormer Karaoke", "becruily"),
    "bs_roformer_karaoke_frazer_becruily.ckpt": ("BS-RoFormer Karaoke", "frazer & becruily"),
    "bs_roformer_karaoke_anvuew.ckpt": ("BS-RoFormer Karaoke", "anvuew"),
}
NOTES = {
    "singer": ("Like the singer", "every pitch change of a held syllable becomes a ~ note"),
    "easier": ("Easier", "only clear, held pitch changes become extra notes"),
    "easiest": ("Easiest", "one note per syllable"),
}
# benchmark config name -> (group, key)
LEAD_FILES = {"UVR_MDXNET_KARA_2.onnx": "kara2", "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": "melkara"}
DEFAULT_RATE = {"separation": 3.0, "pitch": 0.3, "lead": 3.0}
BENCH_AUDIO_S = 170.0   # mean length of the benchmark songs
BENCH_OVERHEAD_MIN = 2.0  # analysis/alignment minutes included in a benchmark run


def bench() -> dict:
    try:
        return json.loads(BENCH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rate(group: str, key: str) -> float:
    """Seconds of work per second of audio (4-core CPU), from the benchmark when measured."""
    b = bench().get(group, {}).get(key)
    if b and b.get("min_per_song"):
        extra = b["min_per_song"] - (BENCH_OVERHEAD_MIN if group != "pitch" else 0.0)
        return max(0.01, extra * 60 / BENCH_AUDIO_S)
    return {"htdemucs": 1.6, "swift": 0.02}.get(key, DEFAULT_RATE[group])


def name(group: str, key: str) -> str:
    table = {"separation": SEPARATION, "pitch": PITCH, "lead": LEAD}[group]
    n, by = table.get(key, (key, ""))
    return f"{n} ({by})" if by else n


def choices() -> dict:
    """For the processing-options window: every model with its benchmark result."""
    b = bench()
    out: dict = {"bench_songs": b.get("songs", []), "baseline": b.get("baseline", {})}
    for group, table in (("separation", SEPARATION), ("pitch", PITCH), ("lead", LEAD)):
        rows = []
        for key, (n, by) in table.items():
            rows.append({"key": key, "name": n, "by": by, "rate": rate(group, key) if key else 0.0,
                         "bench": b.get(group, {}).get(key)})
        rows.sort(key=lambda r: (r["bench"] is None, -(r["bench"] or {}).get("score", 0)))
        if group == "lead":
            rows.sort(key=lambda r: r["key"] != "")
        out[group] = rows
    out["notes"] = [{"key": k, "name": n, "note": note} for k, (n, note) in NOTES.items()]
    return out
