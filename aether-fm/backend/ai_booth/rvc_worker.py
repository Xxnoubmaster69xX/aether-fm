"""
Standalone Out-of-Process RVC v2 Worker (rvc_worker.py)
--------------------------------------------------------
Runs in a separate OS process to guarantee 100% memory and CUDA/cuDNN DLL isolation
from the main AetherFM onnxruntime-gpu audio clock.
"""

import argparse
import os
import sys
from pathlib import Path

# Force CPU mode for RVC so it avoids Blackwell sm_120 cuDNN kernel collisions
os.environ["CUDA_VISIBLE_DEVICES"] = ""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--index", default="")
    parser.add_argument("--tag", default="char")
    parser.add_argument("--pitch", type=int, default=0)
    args = parser.parse_args()

    from infer_rvc_python import BaseLoader

    backend_dir = Path(__file__).resolve().parent.parent
    rmvpe_path = str(backend_dir / "models" / "rvc_weights" / "base" / "rmvpe.pt")

    loader = BaseLoader(only_cpu=True, rmvpe_path=rmvpe_path)
    loader.apply_conf(
        tag=args.tag,
        file_model=args.model,
        file_index=args.index,
        pitch_algo="rmvpe",
        pitch_lvl=args.pitch,
        index_influence=0.75,
    )

    out_files = loader([args.input], tag_list=[args.tag], show_progress=False)
    if out_files and os.path.exists(out_files[0]):
        gen_path = out_files[0]
        if gen_path != args.output:
            import shutil
            shutil.move(gen_path, args.output)
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
