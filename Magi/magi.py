import torch
import json
import os
import argparse
import numpy as np
import warnings
import transformers.modeling_utils
from PIL import Image
from transformers import AutoModel

# --- 1. CLEANUP CONFIGURATION ---
warnings.filterwarnings("ignore")

# --- 2. COMPATIBILITY PATCH ---
_old_mark_tied = transformers.modeling_utils.PreTrainedModel.mark_tied_weights_as_initialized

def _patched_mark_tied(self):
    if not hasattr(self, 'all_tied_weights_keys'):
        self.all_tied_weights_keys = {}
    return _old_mark_tied(self)

transformers.modeling_utils.PreTrainedModel.mark_tied_weights_as_initialized = _patched_mark_tied

# --- 3. HARDWARE SETTINGS ---
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

def load_model():
    print(f"Loading MAGI v2 on {DEVICE}...")
    model = AutoModel.from_pretrained(
        "ragavsachdeva/magiv2", 
        trust_remote_code=True, 
    )
    
    if DEVICE == "cuda":
        # Convert model weights to half precision (FP16)
        model = model.half().to(DEVICE)
    
    model.eval()
    return model

def get_panels(image_path, model):
    # 1. Load Image
    img = Image.open(image_path).convert("RGB")
    
    # 2. Resize (Safety for 4GB VRAM)
    max_dim = 800
    if max(img.size) > max_dim:
        print(f"Resizing input image to {max_dim}px...")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    
    # 3. Convert to Numpy
    img_numpy = np.array(img)
    
    # 4. Inference with Autocast (THE FIX)
    with torch.no_grad():
        # This context manager fixes the Float32 input -> Float16 weight error
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            results = model.predict_detections_and_associations([img_numpy])
        
    return results[0]['panels']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', required=True)
    args = parser.parse_args()
    
    try:
        model = load_model()
        if os.path.exists(args.input):
            print(f"Analyzing {args.input}...")
            panels = get_panels(args.input, model)
            
            output = {"panels": [[int(c) for c in box] for box in panels]}
            with open("panels.json", "w") as f:
                json.dump(output, f, indent=4)
            
            print(f"✅ Success: {len(panels)} panels detected.")
            print("Saved to panels.json")
        else:
            print(f"Error: File {args.input} not found.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Execution failed: {e}")

if __name__ == "__main__":
    main()
