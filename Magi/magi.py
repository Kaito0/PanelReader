import torch
import json
import os
import argparse
import numpy as np
import warnings
import transformers.modeling_utils
from PIL import Image
from transformers import AutoModel

# --- 1. CONFIGURATION ---
warnings.filterwarnings("ignore")

_old_mark_tied = transformers.modeling_utils.PreTrainedModel.mark_tied_weights_as_initialized
def _patched_mark_tied(self):
    if not hasattr(self, 'all_tied_weights_keys'):
        self.all_tied_weights_keys = {}
    return _old_mark_tied(self)
transformers.modeling_utils.PreTrainedModel.mark_tied_weights_as_initialized = _patched_mark_tied

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

def load_model():
    model = AutoModel.from_pretrained("ragavsachdeva/magiv2", trust_remote_code=True)
    if DEVICE == "cuda":
        model = model.half().to(DEVICE)
    model.eval()
    return model

def boxes_overlap(box1, box2, threshold=0):
    """Check if two boxes [x1, y1, x2, y2] overlap spatially."""
    return not (box1[2] < box2[0] - threshold or 
                box1[0] > box2[2] + threshold or 
                box1[3] < box2[1] - threshold or 
                box1[1] > box2[3] + threshold)

def get_inclusive_panels(image_path, model):
    img = Image.open(image_path).convert("RGB")
    # Using 1024 for better resolution on small text bubbles
    max_dim = 800
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    
    img_numpy = np.array(img)
    
    with torch.no_grad():
        context = torch.autocast(device_type="cuda", dtype=torch.float16) if DEVICE == "cuda" else torch.inference_mode()
        with context:
            results = model.predict_detections_and_associations([img_numpy])
    
    res = results[0]
    panels = res.get('panels', [])
    texts = res.get('texts', []) # These are the speech bubbles
    associations = res.get('associations', [])

    final_panels = []

    for p_idx, panel_box in enumerate(panels):
        x1, y1, x2, y2 = panel_box
        
        # 1. Expand based on Model Associations
        associated_text_indices = [a[1] for a in associations if a[0] == p_idx]
        
        # 2. Expand based on Spatial Overlap (The "Safety Net")
        # This catches bubbles that touch the panel but weren't 'linked' by the AI
        for t_idx, text_box in enumerate(texts):
            if t_idx in associated_text_indices or boxes_overlap(panel_box, text_box, threshold=5):
                x1 = min(x1, text_box[0])
                y1 = min(y1, text_box[1])
                x2 = max(x2, text_box[2])
                y2 = max(y2, text_box[3])
        
        final_panels.append([int(x1), int(y1), int(x2), int(y2)])

    return final_panels

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', required=True)
    args = parser.parse_args()
    
    try:
        model = load_model()
        if os.path.exists(args.input):
            panel_coords = get_inclusive_panels(args.input, model)
            
            # Output ONLY panel coordinates
            output = {"panels": panel_coords}
            
            with open("panels.json", "w") as f:
                json.dump(output, f, indent=4)
            
            print(f"✅ Success: Processed {len(panel_coords)} panels.")
            print("The coordinates now include overlapping speech bubbles.")
        else:
            print(f"Error: File {args.input} not found.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()