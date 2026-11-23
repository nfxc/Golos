import shutil
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).parent
MODELS = ROOT / "models"


def fetch(repo, folder, files):
    target = MODELS / folder
    if all((target / name).exists() for name in files):
        print(f"{folder} уже на месте")
        return
    target.mkdir(parents=True, exist_ok=True)
    print(f"Качаю {folder}")
    snapshot_download(repo_id=repo, local_dir=target, allow_patterns=files)
    shutil.rmtree(target / ".cache", ignore_errors=True)


fetch(
    "istupakov/gigaam-v3-onnx",
    "gigaam-v3-e2e-ctc",
    ["config.json", "v3_e2e_ctc.int8.onnx", "v3_e2e_ctc_vocab.txt"],
)
fetch(
    "Systran/faster-whisper-small",
    "faster-whisper-small",
    ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
)
print("Готово")
